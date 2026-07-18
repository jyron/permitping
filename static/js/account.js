const token = new URLSearchParams(location.search).get("t");
const $ = (id) => document.getElementById(id);

function esc(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function statusClass(status) {
  const s = (status || "").toLowerCase();
  if (/(approved|finaled|final|issued|passed|complete)/.test(s)) return "status-green";
  if (/(correction|denied|hold|expired|action|failed|revoked)/.test(s)) return "status-red";
  if (/(review|received|pending|inspection|submitted)/.test(s)) return "status-amber";
  return "";
}

function ago(iso) {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

async function api(path, options = {}) {
  const sep = path.includes("?") ? "&" : "?";
  const res = await fetch(`${path}${sep}t=${encodeURIComponent(token)}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Something went wrong. Try again.");
  return data;
}

function showLogin() {
  $("login-section").style.display = "";
  $("account-sub").textContent = "Sign in to manage your monitored permits.";
  $("link-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await fetch("/api/account/link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: $("link-email").value }),
      });
      const data = await res.json();
      $("link-msg").innerHTML = `<div class="notice ok">${esc(data.message)}</div>`;
    } catch {
      $("link-msg").innerHTML = `<div class="notice error">Could not send the link. Try again.</div>`;
    }
  });
}

function monitorRow(m) {
  const cityName = (window.jurisdictionNames || {})[m.jurisdiction] || m.jurisdiction;
  return `
    <tr class="${m.paused ? "paused-row" : ""}" data-id="${m.id}">
      <td>
        <div class="permit-cell">${esc(m.permit_number)}</div>
        <div class="muted">${esc(m.address)}</div>
      </td>
      <td>${esc(cityName)}</td>
      <td><span class="status-badge ${statusClass(m.current_status)}">${esc(m.current_status)}</span>
          ${m.paused ? '<div class="muted">paused</div>' : ""}</td>
      <td>${ago(m.last_checked_at)}</td>
      <td>
        <div class="actions">
          <button class="small secondary" data-action="${m.paused ? "resume" : "pause"}">${m.paused ? "Resume" : "Pause"}</button>
          <button class="small secondary" data-action="history">History</button>
          <a class="btn small secondary" href="${esc(m.portal_url)}" rel="nofollow">Official record</a>
          <button class="small secondary" data-action="delete">Remove</button>
        </div>
        <div class="history-slot"></div>
      </td>
    </tr>`;
}

async function loadAccount() {
  const account = await api("/api/account");
  $("account-section").style.display = "";
  const limit = account.monitor_limit === null ? "unlimited" : account.monitor_limit;
  $("account-sub").textContent =
    `${account.email} — ${account.plan} plan, ${account.active_monitors} of ${limit} active permits`;
  $("new-email").value = account.email;
  $("monitor-rows").innerHTML = account.monitors.length
    ? account.monitors.map(monitorRow).join("")
    : `<tr><td colspan="5" class="muted">No permits yet. Add one below.</td></tr>`;
}

async function loadJurisdictions() {
  const res = await fetch("/api/jurisdictions");
  const list = await res.json();
  window.jurisdictionNames = Object.fromEntries(list.map((j) => [j.slug, j.city]));
  $("add-jurisdiction").innerHTML = list
    .map((j) => `<option value="${j.slug}">${esc(j.name)}</option>`)
    .join("");
}

$("monitor-rows")?.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const row = btn.closest("tr");
  const id = row.dataset.id;
  const action = btn.dataset.action;
  btn.disabled = true;
  try {
    if (action === "pause" || action === "resume") {
      await api(`/api/monitors/${id}/${action}`, { method: "POST" });
      await loadAccount();
    } else if (action === "delete") {
      await api(`/api/monitors/${id}`, { method: "DELETE" });
      await loadAccount();
    } else if (action === "history") {
      const slot = row.querySelector(".history-slot");
      if (slot.innerHTML) { slot.innerHTML = ""; btn.disabled = false; return; }
      const events = await api(`/api/monitors/${id}/events`);
      slot.innerHTML = events.length
        ? `<ul class="history">${events
            .map((ev) => `<li>${new Date(ev.changed_at).toLocaleString()} — ${esc(ev.previous_status || "(start)")} → <b>${esc(ev.new_status)}</b></li>`)
            .join("")}</ul>`
        : `<p class="muted">No status changes recorded yet.</p>`;
      btn.disabled = false;
    }
  } catch (err) {
    alert(err.message);
    btn.disabled = false;
  }
});

$("add-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  $("add-msg").innerHTML = "";
  try {
    await api("/api/account/monitors", {
      method: "POST",
      body: JSON.stringify({
        jurisdiction: $("add-jurisdiction").value,
        permit_number: $("add-permit").value,
      }),
    });
    $("add-permit").value = "";
    $("add-msg").innerHTML = `<div class="notice ok">Permit added.</div>`;
    await loadAccount();
  } catch (err) {
    $("add-msg").innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
  }
});

$("email-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  $("email-msg").innerHTML = "";
  try {
    await api("/api/account/email", {
      method: "POST",
      body: JSON.stringify({ email: $("new-email").value }),
    });
    $("email-msg").innerHTML = `<div class="notice ok">Email updated.</div>`;
    await loadAccount();
  } catch (err) {
    $("email-msg").innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
  }
});

if (!token) {
  showLogin();
} else {
  loadJurisdictions()
    .then(loadAccount)
    .catch((err) => {
      showLogin();
      $("link-msg").innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
    });
}
