// One omnibox, every page. The page sets the scope (<body data-jurisdiction>
// pins it to a city); the dropdown does all disambiguation — address
// suggestions, permit-number candidates, ZIP -> city, and capability hints.
// Selecting anything navigates to a real URL:
//   /{city}                      city page
//   /{city}/address/{addr-slug}  every permit at an address
//   /{city}/permit/{number}      one permit's status

const scopeSlug = document.body.dataset.jurisdiction || null;
const $ = (id) => document.getElementById(id);

// analytics: window.ph is defined (possibly as a no-op) by /static/js/ph.js
const ph = (event, props) => window.ph && window.ph(event, props);
const failReason = (status) =>
  ({ 402: "plan_limit", 404: "not_found", 409: "already_monitored" }[status] || "error");

function esc(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || "Something went wrong. Try again.");
    err.status = res.status;
    throw err;
  }
  return data;
}

// mirror of slugify_address() in app/services/addresses.py
function slugifyAddress(address) {
  return (address.toLowerCase().match(/[a-z0-9]+/g) || []).join("-");
}

let JURISDICTIONS = [];
const jurisdictionsReady = api("/api/jurisdictions").then((list) => {
  JURISDICTIONS = list;
  return list;
});

function cityForZip(zip) {
  return JURISDICTIONS.find(
    (j) => j.zips.includes(zip) || j.zip_prefixes.some((p) => zip.startsWith(p))
  );
}

function permitCandidates(text) {
  const ordered = scopeSlug
    ? [...JURISDICTIONS].sort((a, b) => (b.slug === scopeSlug) - (a.slug === scopeSlug))
    : JURISDICTIONS;
  return ordered.filter((j) => new RegExp(j.permit_pattern).test(text)).slice(0, 3);
}

// ---- Omnibox ---------------------------------------------------------------

function initOmnibox() {
  const input = $("omnibox-input");
  if (!input) return;
  const list = $("omnibox-list");
  const hint = $("omnibox-hint");
  let rows = [];
  let active = -1;
  let timer = null;
  let controller = null;
  let requestSeq = 0;
  let searchTimer = null;
  let unavailableHintSent = false;

  jurisdictionsReady.then(() => {
    const scope = JURISDICTIONS.find((j) => j.slug === scopeSlug);
    const addressCities = JURISDICTIONS.filter((j) => j.address_search);
    if (!scope) {
      input.placeholder = "1060 W Addison St — or a permit number";
      hint.textContent =
        `Address search: ${addressCities.map((j) => j.city).join(", ")} · ` +
        "permit numbers work for every supported city.";
    } else if (scope.address_search) {
      input.placeholder = `Address in ${scope.city} — or a permit number like ${scope.permit_example}`;
      hint.textContent = `Searches ${scope.city} addresses and permit numbers.`;
    } else {
      input.placeholder = `Permit number, e.g. ${scope.permit_example}`;
      hint.textContent =
        `${scope.city} address search isn't available yet — ` +
        `look up by permit number (they look like ${scope.permit_example}).`;
    }
  });

  const close = () => {
    list.hidden = true;
    list.innerHTML = "";
    rows = [];
    active = -1;
    input.setAttribute("aria-expanded", "false");
  };

  const render = () => {
    if (!rows.length) return close();
    list.innerHTML = rows
      .map((r, i) => {
        const cls = [r.type === "hint" ? "row-hint" : "", i === active ? "active" : ""]
          .filter(Boolean).join(" ");
        return `<li role="option" ${cls ? `class="${cls}"` : ""} ${i === active ? 'aria-selected="true"' : ""}>${r.html}</li>`;
      })
      .join("");
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    [...list.children].forEach((li, i) => {
      li.addEventListener("mousedown", (e) => {
        e.preventDefault();
        choose(rows[i]);
      });
    });
  };

  const choose = (row) => {
    if (!row || !row.href) return;
    ph("suggestion_selected", {
      type: row.type,
      city: row.href.split("/")[1],
      scope: scopeSlug,
    });
    close();
    window.location = row.href;
  };

  const buildStaticRows = (text) => {
    const built = [];
    const upper = text.toUpperCase();
    const zipCity = /^\d{5}$/.test(text) ? cityForZip(text) : null;
    if (zipCity) {
      built.push({
        type: "city",
        href: `/${zipCity.slug}`,
        html: `${esc(text)} is ${esc(zipCity.city)}, ${esc(zipCity.state)} <span class="suggestion-city">open city search</span>`,
      });
    }
    if (/^[A-Z0-9][A-Z0-9-]{3,}$/.test(upper) && /\d/.test(upper) && !/\s/.test(text)) {
      for (const j of permitCandidates(upper)) {
        built.push({
          type: "permit",
          href: `/${j.slug}/permit/${encodeURIComponent(upper)}`,
          html: `Look up permit <b>${esc(upper)}</b> <span class="suggestion-city">${esc(j.city)}, ${esc(j.state)}</span>`,
        });
      }
    }
    return built;
  };

  const addressish = (text) =>
    /\s/.test(text.trim()) || /^\d+$/.test(text.trim());

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const text = input.value.trim();
    if (text.length < 2) return close();

    rows = buildStaticRows(text);
    const scope = JURISDICTIONS.find((j) => j.slug === scopeSlug);
    const canSearchAddresses = scope ? scope.address_search : true;

    if (addressish(text) && !canSearchAddresses && scope) {
      rows.push({
        type: "hint",
        html: `${esc(scope.city)} address search isn't available yet — permit numbers look like <b>${esc(scope.permit_example)}</b>`,
      });
      if (!unavailableHintSent) {
        unavailableHintSent = true;
        ph("address_search_unavailable", { city: scopeSlug });
      }
    }
    render();

    // one search_performed per typing pause; rows reflects whatever the
    // async address fetch has rendered by then
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      ph("search_performed", {
        scope: scopeSlug,
        kind: /^\d{5}$/.test(text) ? "zip" : addressish(text) ? "address" : "permit",
        query_len: text.length,
        results: rows.filter((r) => r.href).length,
      });
    }, 1000);

    if (!addressish(text) || !canSearchAddresses || text.length < 4) return;
    const seq = ++requestSeq;
    timer = setTimeout(async () => {
      controller?.abort();
      controller = new AbortController();
      try {
        const url = `/api/addresses/suggest?q=${encodeURIComponent(text)}` +
          (scopeSlug ? `&city=${encodeURIComponent(scopeSlug)}` : "");
        const res = await fetch(url, { signal: controller.signal });
        if (!res.ok || seq !== requestSeq) return;
        const suggestions = await res.json();
        const addressRows = suggestions.map((s) => ({
          type: "address",
          href: `/${s.slug}/address/${slugifyAddress(s.address)}`,
          html: `${esc(s.address)} <span class="suggestion-city">${esc(s.city)}, ${esc(s.state)}</span>`,
        }));
        if (!addressRows.length && !rows.some((r) => r.type !== "hint")) {
          addressRows.push({
            type: "hint",
            html: "No matching addresses in supported cities yet — keep typing, or try a permit number.",
          });
        }
        rows = [...addressRows, ...rows.filter((r) => r.type !== "hint" || addressRows.length === 0)];
        active = -1;
        render();
      } catch (err) {
        if (err.name !== "AbortError") close();
      }
    }, 150);
  });

  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    const selectable = rows.map((r, i) => (r.href ? i : -1)).filter((i) => i >= 0);
    if (!selectable.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const pos = selectable.indexOf(active);
      const next = e.key === "ArrowDown"
        ? selectable[(pos + 1) % selectable.length]
        : selectable[(pos - 1 + selectable.length) % selectable.length];
      active = next;
      render();
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(rows[active >= 0 ? active : selectable[0]]);
    } else if (e.key === "Escape") {
      close();
    }
  });

  input.addEventListener("blur", () => setTimeout(close, 150));
}

// ---- Permit page: monitor signup ------------------------------------------

function initMonitorForm() {
  const form = $("monitor-form");
  const permitNumber = document.body.dataset.permit;
  if (!form || !permitNumber) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("monitor-btn");
    btn.disabled = true;
    $("monitor-msg").innerHTML = "";
    ph("monitor_submit", { city: scopeSlug, permit_number: permitNumber });
    try {
      await api("/api/monitors", {
        method: "POST",
        body: JSON.stringify({
          jurisdiction: scopeSlug,
          permit_number: permitNumber,
          email: $("monitor-email").value,
        }),
      });
      window.phIdentify && window.phIdentify($("monitor-email").value);
      ph("monitor_created", {
        city: scopeSlug,
        permit_number: permitNumber,
        source: "permit_page",
      });
      $("monitor-msg").innerHTML =
        `<div class="notice ok">Monitoring started. Check your email for a link to manage your permits.</div>`;
      form.style.display = "none";
    } catch (err) {
      ph("monitor_create_failed", {
        city: scopeSlug,
        reason: failReason(err.status),
        source: "permit_page",
      });
      $("monitor-msg").innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
      btn.disabled = false;
    }
  });
}

// ---- Landing page: city directory -----------------------------------------

async function loadCityLinks() {
  const links = $("city-links");
  if (!links) return;
  const list = await jurisdictionsReady;
  links.innerHTML = list
    .map((j) => `<a href="/${j.slug}">${esc(j.city)}, ${esc(j.state)}</a>`)
    .join("");
}

initOmnibox();
initMonitorForm();
loadCityLinks();
