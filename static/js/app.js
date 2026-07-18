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
      <p class="muted" style="margin-top:0">Permit found — ${esc(record.jurisdiction_name)}</p>
      <p class="permit-number">${esc(record.permit_number)}</p>
      <p class="result-meta">${esc(record.address)}<br>${esc(record.description)}</p>
      <span class="status-badge ${statusClass(record.status)}">${esc(record.status)}</span>
      <div class="detail-grid">
        <div><b>Status date</b>${esc(record.status_date || "—")}</div>
        <div><b>Jurisdiction</b>${esc(record.jurisdiction_name)}</div>
        <div><b>Source</b>${esc(d.source || "official municipal record")}</div>
        <div><b>Data freshness</b>${esc(d.freshness || "—")}</div>
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
