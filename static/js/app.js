// Shared by the landing page and city pages: lookup + monitor signup.
// City pages pin the jurisdiction via <body data-jurisdiction>; the landing
// page sends whatever the visitor typed (city name or ZIP).

const fixedJurisdiction = document.body.dataset.jurisdiction || null;
const $ = (id) => document.getElementById(id);

function statusClass(status) {
  const s = (status || "").toLowerCase();
  if (/(approved|finaled|final|issued|passed|complete|done)/.test(s)) return "status-green";
  if (/(correction|denied|hold|expired|action|failed|revoked)/.test(s)) return "status-red";
  if (/(review|received|pending|inspection|submitted|applied)/.test(s)) return "status-amber";
  return "";
}

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
  if (!res.ok) throw new Error(data.detail || "Something went wrong. Try again.");
  return data;
}

async function loadCityLinks() {
  const links = $("city-links");
  if (!links) return;
  const list = await api("/api/jurisdictions");
  links.innerHTML = list
    .map((j) => `<a href="/${j.slug}">${esc(j.city)}, ${esc(j.state)}</a>`)
    .join("");
}

function renderResult(record) {
  const d = record.details || {};
  const retrieved = d.retrieved_seconds_ago ?? 0;
  const retrievedText =
    retrieved < 5 ? "just now"
    : retrieved < 120 ? `${retrieved} seconds ago`
    : `${Math.round(retrieved / 60)} minutes ago`;
  $("result").innerHTML = `
    <div class="card">
      <p class="muted" style="margin-top:0">Permit found: ${esc(record.jurisdiction_name)}</p>
      <p class="permit-number">${esc(record.permit_number)}</p>
      <p class="result-meta">${esc(record.address)}<br>${esc(record.description)}</p>
      <span class="status-badge ${statusClass(record.status)}">${esc(record.status)}</span>
      <div class="detail-grid">
        <div><b>Status date</b>${esc(record.status_date || "n/a")}</div>
        <div><b>Jurisdiction</b>${esc(record.jurisdiction_name)}</div>
        <div><b>Source</b>${esc(d.source || "official municipal record")}</div>
        <div><b>Data freshness</b>${esc(d.freshness || "n/a")}</div>
      </div>
      <a class="btn secondary" href="${esc(record.portal_url)}" rel="nofollow">View official record</a>
      <p class="retrieved">Retrieved from the official source ${retrievedText}.</p>
    </div>
    <div class="card upsell">
      <h3>Want to know when this changes?</h3>
      <p>Permit Ping will check this record daily and email you when its status changes. Free for up to 3 permits.</p>
      <form id="monitor-form">
        <label for="monitor-email">Email address</label>
        <input id="monitor-email" type="email" placeholder="permits@company.com" required>
        <button type="submit" id="monitor-btn">Monitor this permit</button>
      </form>
      <div id="monitor-msg"></div>
    </div>`;

  $("monitor-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("monitor-btn");
    btn.disabled = true;
    $("monitor-msg").innerHTML = "";
    try {
      await api("/api/monitors", {
        method: "POST",
        body: JSON.stringify({
          jurisdiction: record.jurisdiction,
          permit_number: record.permit_number,
          email: $("monitor-email").value,
        }),
      });
      $("monitor-msg").innerHTML =
        `<div class="notice ok">Monitoring started. Check your email for a link to manage your permits.</div>`;
      $("monitor-form").style.display = "none";
    } catch (err) {
      $("monitor-msg").innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
      btn.disabled = false;
    }
  });
}

// ---- Address autocomplete (landing page only) ----------------------------
// Suggestions come from the cities' own permit datasets, so every suggestion
// is an address that actually has permits on file.
function initAddressSearch() {
  const input = $("address-input");
  if (!input) return;
  const list = $("address-suggestions");
  let items = [];
  let active = -1;
  let timer = null;
  let controller = null;

  const close = () => {
    list.hidden = true;
    list.innerHTML = "";
    items = [];
    active = -1;
    input.setAttribute("aria-expanded", "false");
  };

  const render = () => {
    if (!items.length) return close();
    list.innerHTML = items
      .map(
        (s, i) =>
          `<li role="option" id="addr-opt-${i}" ${i === active ? 'class="active" aria-selected="true"' : ""}>
             ${esc(s.address)} <span class="suggestion-city">${esc(s.city)}, ${esc(s.state)}</span></li>`
      )
      .join("");
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    [...list.children].forEach((li, i) => {
      li.addEventListener("mousedown", (e) => {
        e.preventDefault();
        select(items[i]);
      });
    });
  };

  async function select(s) {
    input.value = s.address;
    close();
    $("address-error").innerHTML = "";
    $("address-permits").innerHTML =
      `<div class="card"><p class="muted" style="margin:0">Looking up permits at ${esc(s.address)}…</p></div>`;
    try {
      const data = await api("/api/addresses/permits", {
        method: "POST",
        body: JSON.stringify({ slug: s.slug, filters: s.filters }),
      });
      renderAddressPermits(s, data);
    } catch (err) {
      $("address-permits").innerHTML = "";
      $("address-error").innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
    }
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 4) return close();
    timer = setTimeout(async () => {
      controller?.abort();
      controller = new AbortController();
      try {
        const res = await fetch(`/api/addresses/suggest?q=${encodeURIComponent(q)}`, {
          signal: controller.signal,
        });
        if (!res.ok) return;
        items = await res.json();
        active = -1;
        render();
      } catch (err) {
        if (err.name !== "AbortError") close();
      }
    }, 250);
  });

  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      active = (active + 1) % items.length;
      render();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      active = (active - 1 + items.length) % items.length;
      render();
    } else if (e.key === "Enter") {
      e.preventDefault();
      select(items[active >= 0 ? active : 0]);
    } else if (e.key === "Escape") {
      close();
    }
  });

  input.addEventListener("blur", () => setTimeout(close, 150));
}

function renderAddressPermits(s, data) {
  const j = data.jurisdiction;
  if (!data.permits.length) {
    $("address-permits").innerHTML =
      `<div class="card"><p class="muted" style="margin:0">No permits on file at ${esc(s.address)} in ${esc(j.city)}, ${esc(j.state)}.</p></div>`;
    return;
  }
  const rows = data.permits
    .map(
      (p) => `
      <div class="permit-row">
        <div class="permit-row-main">
          <b>${esc(p.permit_number)}</b>
          <span class="status-badge ${statusClass(p.status)}">${esc(p.status)}</span>
        </div>
        <p class="result-meta">${esc(p.description || "")}${p.date ? ` · ${esc(p.date)}` : ""}</p>
        <button class="btn secondary" data-permit="${esc(p.permit_number)}">Full status &amp; monitoring</button>
      </div>`
    )
    .join("");
  $("address-permits").innerHTML = `
    <div class="card">
      <p class="muted" style="margin-top:0">${data.permits.length} permit${data.permits.length === 1 ? "" : "s"} on file at</p>
      <p class="permit-number">${esc(s.address)} — ${esc(j.city)}, ${esc(j.state)}</p>
      ${rows}
    </div>`;
  [...document.querySelectorAll("#address-permits [data-permit]")].forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Loading…";
      try {
        const record = await api("/api/lookup", {
          method: "POST",
          body: JSON.stringify({ location: j.slug, permit_number: btn.dataset.permit }),
        });
        renderResult(record);
        $("result").scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (err) {
        $("address-error").innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
      } finally {
        btn.disabled = false;
        btn.textContent = "Full status & monitoring";
      }
    });
  });
}

initAddressSearch();

$("lookup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("lookup-btn");
  btn.disabled = true;
  btn.textContent = "Checking…";
  $("lookup-error").innerHTML = "";
  $("result").innerHTML = "";
  try {
    const record = await api("/api/lookup", {
      method: "POST",
      body: JSON.stringify({
        location: fixedJurisdiction || $("location").value,
        permit_number: $("permit-number").value,
      }),
    });
    renderResult(record);
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    $("lookup-error").innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Check permit status";
  }
});

loadCityLinks();
