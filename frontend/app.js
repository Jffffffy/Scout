// Scout frontend: plain JS, no build step.
const API = "";  // same origin as the FastAPI server

const CAT_ICON = { study: "📚", dining: "🍜", gym: "🏋️", laundry: "🧺", office_hours: "🙋" };
const AVAIL_COLOR = { high: "#16a34a", medium: "#eab308", low: "#dc2626" };
const AVAIL_LABEL = { high: "Easy to use", medium: "Busy", low: "Full" };
const EXAMPLES = [
  "Quiet spot with an outlet, near Tepper",
  "Where can 4 of us use a whiteboard?",
  "Shortest lunch line right now",
  "Is there a free washer?",
];

let user = null;
let places = [];
let map, markers = {};
let myPos = null;

// ---------- storage (wrapped: may be unavailable) ----------
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};

// ---------- api ----------
async function api(path, opts = {}) {
  const res = await fetch(API + path, opts);
  let data = null;
  try { data = await res.json(); } catch {}
  if (!res.ok) {
    const err = new Error((data && data.detail) || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function setBalance(n) {
  if (user) user.credits = n;
  $("balance").textContent = n ?? "–";
}

function toast(msg, kind = "info") {
  const colors = { info: "bg-stone-900", good: "bg-green-700", bad: "bg-red-700" };
  const el = document.createElement("div");
  el.className = `fixed left-1/2 -translate-x-1/2 bottom-6 z-[3000] ${colors[kind]} text-white text-sm px-4 py-2.5 rounded-xl shadow-lg fade-in max-w-[90vw]`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function freshnessText(p) {
  if (!p.has_report) return "No report yet";
  if (!p.fresh) return `Stale · ${p.minutes_old} min old`;
  return p.minutes_old === 0 ? "Updated just now" : `Updated ${p.minutes_old} min ago`;
}

// ---------- account ----------
let authMode = "signup";

function setAuthMode(mode) {
  authMode = mode;
  const on = "rounded-lg py-2 bg-white shadow", off = "rounded-lg py-2 text-stone-500";
  $("auth-tab-signup").className = mode === "signup" ? on : off;
  $("auth-tab-login").className = mode === "login" ? on : off;
  $("auth-submit").textContent = mode === "signup" ? "Create account · get 🪙5" : "Log in";
  $("auth-pass").autocomplete = mode === "signup" ? "new-password" : "current-password";
  $("auth-error").classList.add("hidden");
}

// Resolves once the user is signed in.
function ensureUser() {
  return new Promise(async (resolve) => {
    const id = store.get("scout_user");
    if (id) {
      try { user = await api(`/api/users/${id}`); setBalance(user.credits); return resolve(); } catch {}
    }
    $("auth").classList.remove("hidden");
    $("auth-tab-signup").onclick = () => setAuthMode("signup");
    $("auth-tab-login").onclick = () => setAuthMode("login");
    $("auth-form").onsubmit = async (ev) => {
      ev.preventDefault();
      const body = JSON.stringify({ username: $("auth-user").value, password: $("auth-pass").value });
      try {
        user = await api(authMode === "signup" ? "/api/signup" : "/api/login", {
          method: "POST", headers: { "Content-Type": "application/json" }, body,
        });
      } catch (e) {
        $("auth-error").textContent = e.message;
        $("auth-error").classList.remove("hidden");
        return;
      }
      store.set("scout_user", user.id);
      setBalance(user.credits);
      $("auth").classList.add("hidden");
      toast(authMode === "signup" ? `Welcome ${user.name}! You got 🪙 ${user.credits} starter credits.` : `Welcome back ${user.name}!`, "good");
      resolve();
    };
  });
}

function logout() {
  try { localStorage.removeItem("scout_user"); } catch {}
  location.reload();
}

function openAccount() {
  if (!user) {  // not signed in yet: show the sign-in box instead
    $("auth").classList.remove("hidden");
    return;
  }
  $("modal-body").innerHTML = `
    <div class="flex justify-between"><span class="text-stone-500">Name</span><b>${esc(user.name)}</b></div>
    <div class="flex justify-between"><span class="text-stone-500">Credits</span><b>🪙 ${user.credits}</b></div>
    <div class="flex justify-between"><span class="text-stone-500">Photos shared</span><b>${user.uploads}</b></div>
    <div>
      <div class="text-stone-500">Your private Shortcut key (keep it secret, paste it into the iPhone Shortcut as <code>user_id</code>)</div>
      <div class="mt-1 flex gap-2">
        <code class="flex-1 rounded-lg bg-stone-100 px-3 py-2 font-mono break-all">${esc(user.id)}</code>
        <button id="copy-id" class="rounded-lg bg-stone-200 px-3 text-sm">Copy</button>
      </div>
    </div>
    <button id="logout" class="w-full rounded-xl border border-stone-300 py-2 text-stone-700">Log out</button>`;
  $("modal").classList.remove("hidden");
  $("copy-id").onclick = () => navigator.clipboard?.writeText(user.id).then(() => toast("Copied"));
  $("logout").onclick = logout;
}

// ---------- tabs ----------
function showTab(name) {
  document.querySelectorAll(".tab-panel").forEach((el) => el.classList.add("hidden"));
  $(`tab-${name}`).classList.remove("hidden");
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("tab-active", b.dataset.tab === name));
  if (name === "map" && map) setTimeout(() => map.invalidateSize(), 50);
  if (name === "snap") loadLeaderboard();
}

// ---------- map ----------
function initMap() {
  map = L.map("map", { zoomControl: true }).setView([40.4433, -79.9436], 17);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
}

function pinHtml(p) {
  const color = p.fresh && p.availability ? AVAIL_COLOR[p.availability] : "#d6d3d1";
  return `<div style="position:relative">
    <div class="pin" style="background:${color}">${CAT_ICON[p.category] || "📍"}</div>
    <div class="bounty">🪙${p.bounty}</div></div>`;
}

function popupHtml(p) {
  const status = p.fresh && p.availability
    ? `<span style="color:${AVAIL_COLOR[p.availability]};font-weight:700">${AVAIL_LABEL[p.availability]}</span>`
    : `<span style="color:#78716c">Unknown right now</span>`;
  return `<div style="min-width:200px">
    <div style="font-weight:700;font-size:14px">${esc(p.name)}</div>
    <div style="margin:2px 0">${status} · <span style="color:#78716c">${freshnessText(p)}</span></div>
    <div style="color:#78716c;font-size:12px">Snap it now for 🪙 ${p.bounty}</div>
    <div id="detail-${p.id}" style="margin-top:6px"></div>
    <button onclick="unlock('${p.id}')" style="margin-top:6px;width:100%;background:#b91c1c;color:white;border-radius:8px;padding:6px;font-weight:600">
      ${p.has_report ? "See details · 🪙1" : "Request a report (free)"}</button>
  </div>`;
}

async function loadPlaces() {
  places = await api("/api/places");
  for (const p of places) {
    const icon = L.divIcon({ html: pinHtml(p), iconSize: [34, 34], iconAnchor: [17, 17] });
    if (markers[p.id]) {
      markers[p.id].setIcon(icon);
      if (!markers[p.id].isPopupOpen()) markers[p.id].setPopupContent(popupHtml(p));
    } else {
      markers[p.id] = L.marker([p.lat, p.lng], { icon }).addTo(map).bindPopup(popupHtml(p));
    }
  }
  const sel = $("snap-place");
  if (sel.options.length <= 1) {
    places.forEach((p) => sel.add(new Option(`${CAT_ICON[p.category] || ""} ${p.name}`, p.id)));
  }
}

function stateHtml(state) {
  if (!state) return "";
  const rows = [];
  const add = (k, v) => rows.push(`<div style="display:flex;justify-content:space-between;gap:8px"><span style="color:#78716c">${k}</span><b>${esc(v)}</b></div>`);
  if (state.summary) rows.push(`<div style="margin-bottom:4px">${esc(state.summary)}</div>`);
  if (state.free_seats != null) add("Free seats", state.free_seats);
  if (state.outlets_available != null) add("Outlet free", state.outlets_available ? "Yes" : "No");
  if (state.whiteboard_free != null) add("Whiteboard free", state.whiteboard_free ? "Yes" : "No");
  if (state.noise) add("Noise", state.noise);
  if (state.line_length != null) add("In line", `${state.line_length} people`);
  if (state.wait_minutes != null) add("Wait", `~${state.wait_minutes} min`);
  if (state.queue_length != null) add("OH queue", state.queue_length);
  (state.equipment || []).forEach((e) => add(e.name, e.free ? "Free" : "In use"));
  (state.machines || []).forEach((m) => {
    const s = m.status === "free" ? "Free" : m.status === "running" ? `Running${m.minutes_left != null ? ` · ${m.minutes_left} min` : ""}` : "Done, clothes inside";
    add(`${m.type === "dryer" ? "Dryer" : "Washer"} ${m.id}`, s);
  });
  return `<div style="font-size:12px;border-top:1px solid #e7e5e4;padding-top:6px">${rows.join("")}</div>`;
}

window.unlock = async (placeId) => {
  try {
    const d = await api(`/api/places/${placeId}?user_id=${encodeURIComponent(user.id)}`);
    setBalance(d.credits);
    const box = $(`detail-${placeId}`);
    if (!d.state) {
      if (box) box.innerHTML = `<div style="font-size:12px;color:#78716c">Request sent. The bounty for this place just went up.</div>`;
      loadPlaces();
      return;
    }
    if (box) box.innerHTML = stateHtml(d.state);
  } catch (e) {
    toast(e.message, "bad");
  }
};

// ---------- ask ----------
function renderExamples() {
  $("ask-examples").innerHTML = EXAMPLES.map(
    (t) => `<button class="ex rounded-full bg-stone-100 px-3 py-1 text-xs hover:bg-stone-200">${esc(t)}</button>`
  ).join("");
  document.querySelectorAll(".ex").forEach((b) => (b.onclick = () => ($("ask-input").value = b.textContent)));
}

async function ask() {
  const query = $("ask-input").value.trim();
  if (!query) return toast("Type what you need first");
  const btn = $("ask-btn");
  btn.disabled = true; btn.textContent = "Scouting…";
  $("ask-results").innerHTML = `<div class="text-sm text-stone-500">Checking every place on campus…</div>`;
  try {
    const body = { user_id: user.id, query, ...(myPos || {}) };
    const d = await api("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    setBalance(d.credits);
    const cards = d.recommendations.map((r, i) => {
      const color = r.fresh && r.availability ? AVAIL_COLOR[r.availability] : "#a8a29e";
      return `<div class="fade-in rounded-2xl bg-white border border-stone-200 p-4 flex gap-3">
        <div class="text-2xl font-bold text-stone-300 w-6">${i + 1}</div>
        <div class="flex-1">
          <div class="flex items-center gap-2">
            <span class="text-lg">${CAT_ICON[r.category] || "📍"}</span>
            <span class="font-semibold">${esc(r.name)}</span>
            <span class="ml-auto w-2.5 h-2.5 rounded-full" style="background:${color}"></span>
          </div>
          <div class="text-sm mt-1">${esc(r.reason)}</div>
          <div class="text-xs text-stone-500 mt-1">${freshnessText(r)}${!r.fresh ? ` · snap it for 🪙${r.bounty}` : ""}</div>
        </div>
        <button class="self-center text-xs rounded-lg bg-stone-100 px-2 py-1" onclick="flyTo('${r.id}')">Map</button>
      </div>`;
    });
    $("ask-results").innerHTML = cards.join("") +
      (d.note ? `<div class="text-xs text-stone-500">${esc(d.note)}</div>` : "") +
      (cards.length ? "" : `<div class="text-sm">No good match yet. Try snapping a nearby place to earn credits.</div>`);
    loadPlaces();
  } catch (e) {
    $("ask-results").innerHTML = "";
    toast(e.message, "bad");
    if (e.status === 402) showTab("snap");
  } finally {
    btn.disabled = false; btn.textContent = "Ask Scout";
  }
}

window.flyTo = (id) => {
  showTab("map");
  const m = markers[id];
  if (m) { map.flyTo(m.getLatLng(), 18); setTimeout(() => m.openPopup(), 400); }
};

// ---------- snap ----------
function onFile() {
  const f = $("snap-file").files[0];
  $("snap-btn").disabled = !f;
  $("snap-filename").textContent = f ? f.name : "";
  if (f) {
    $("snap-preview").src = URL.createObjectURL(f);
    $("snap-preview").classList.remove("hidden");
  }
}

function step(text, done = false) {
  const el = document.createElement("div");
  el.className = "fade-in flex items-center gap-2";
  el.innerHTML = `<span>${done ? "✅" : "⏳"}</span><span>${esc(text)}</span>`;
  $("snap-steps").appendChild(el);
  return el;
}

async function upload() {
  const f = $("snap-file").files[0];
  if (!f) return;
  $("snap-btn").disabled = true;
  $("snap-steps").innerHTML = "";
  $("snap-result").innerHTML = "";

  const fd = new FormData();
  fd.append("photo", f);
  fd.append("user_id", user.id);
  if ($("snap-place").value) fd.append("place_id", $("snap-place").value);
  if (myPos) { fd.append("lat", myPos.lat); fd.append("lng", myPos.lng); }

  const s = [step("Blurring faces and removing photo metadata"), step("AI is checking the photo"), step("Updating the map")];
  try {
    const d = await api("/api/upload", { method: "POST", body: fd });
    s.forEach((el) => (el.firstChild.textContent = "✅"));
    step(d.accepted ? "Photo deleted. Only the result is kept." : "Photo deleted.", true);
    setBalance(d.credits);
    $("snap-result").innerHTML = `<div class="fade-in rounded-xl p-3 ${d.accepted ? "bg-green-50 text-green-900" : "bg-red-50 text-red-900"}">
      <div class="font-semibold">${esc(d.message)}</div>
      ${d.summary ? `<div class="text-sm mt-1">AI saw: ${esc(d.summary)}</div>` : ""}
      ${d.faces_blurred ? `<div class="text-xs mt-1">${d.faces_blurred} face(s) blurred before analysis.</div>` : ""}
      ${d.mock ? `<div class="text-xs mt-1 opacity-70">Mock AI mode (no OPENROUTER_KEY).</div>` : ""}
      ${d.accepted && d.analysis ? `<div class="mt-2 bg-white/70 rounded-lg p-2">${stateHtml(d.analysis)}</div>` : ""}
      ${d.analysis ? `<details class="mt-2 text-xs"><summary class="cursor-pointer opacity-70">Raw AI output (for testing)</summary>
        <pre class="mt-1 whitespace-pre-wrap break-all bg-white/70 rounded p-2">${esc(JSON.stringify(d.analysis, null, 2))}</pre></details>` : ""}
    </div>`;
    if (d.accepted) toast(d.message, "good");
    $("snap-file").value = ""; $("snap-preview").classList.add("hidden"); $("snap-filename").textContent = "";
    loadPlaces(); loadLeaderboard(); refreshUser();
  } catch (e) {
    $("snap-steps").innerHTML = "";
    toast(e.message, "bad");
    $("snap-btn").disabled = false;
  }
}

async function loadLeaderboard() {
  try {
    const rows = await api("/api/leaderboard");
    $("leaderboard").innerHTML = rows.length
      ? rows.map((r, i) => `<li class="flex justify-between"><span>${["🥇", "🥈", "🥉"][i] || `${i + 1}.`} ${esc(r.name)}</span><span class="text-stone-500">${r.uploads} photos</span></li>`).join("")
      : `<li class="text-stone-500">No photos yet. Be the first scout!</li>`;
  } catch {}
}

async function refreshUser() {
  try { user = await api(`/api/users/${user.id}`); setBalance(user.credits); } catch {}
}

// ---------- status banner ----------
async function loadStatus() {
  try {
    const s = await api("/api/status");
    const notes = [];
    if (s.mock_ai) notes.push("Mock AI mode: add OPENROUTER_KEY to .env for real analysis.");
    if (!s.face_blur_ready) notes.push("Face blurring unavailable: uploads will be refused.");
    if (notes.length) {
      $("banner").innerHTML = `<div class="rounded-xl bg-amber-50 border border-amber-200 text-amber-900 text-xs px-3 py-2">${notes.map(esc).join("<br>")}</div>`;
      $("banner").classList.remove("hidden");
    }
  } catch {}
}

// ---------- boot ----------
function locate() {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(
    (pos) => { myPos = { lat: pos.coords.latitude, lng: pos.coords.longitude }; },
    () => {}, { enableHighAccuracy: true, timeout: 8000 }
  );
}

document.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  $("account-btn").onclick = openAccount;
  $("modal-close").onclick = () => $("modal").classList.add("hidden");
  $("ask-btn").onclick = ask;
  $("snap-file").onchange = onFile;
  $("snap-btn").onclick = upload;
  renderExamples();
  initMap();
  showTab("map");
  locate();
  loadStatus();
  await ensureUser();
  await loadPlaces();
  setInterval(loadPlaces, 15000);  // live updates
});
