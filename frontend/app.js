// Scout frontend: plain JS, no build step.
const API = "";  // same origin as the FastAPI server

const CAT_ICON = { study: "📚", dining: "🍜", lounge: "🛋️", gym: "🏋️", laundry: "🧺" };
const CAT_LABEL = { study: "Study", dining: "Dining", lounge: "Dorm lounges", gym: "Gym", laundry: "Laundry" };
const LEVEL_COLOR = { not_busy: "#16a34a", moderate: "#eab308", busy: "#dc2626" };
const LEVEL_LABEL = { not_busy: "Not busy", moderate: "Moderate", busy: "Busy" };
const CAMPUS = [40.4433, -79.9436];
const EXAMPLES = [
  "Quiet spot with an outlet, near Tepper",
  "Where can 4 of us use a whiteboard?",
  "Shortest lunch line right now",
  "Is there a free washer?",
];

let user = null;
let places = [];
let map, markers = {};
let myPos = null, myDot = null;
let activeCat = "all";
let fix = null;        // {placeId, lat, lng, label} while fixing a pin
let fixMarker = null;  // draggable preview marker
let pickMode = false;  // waiting for a tap on the map

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
  map = L.map("map", { zoomControl: true }).setView(CAMPUS, 16);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  // two buttons bottom-right: 📍 my location, 🏫 back to campus
  const Ctl = L.Control.extend({
    options: { position: "bottomright" },
    onAdd() {
      const box = L.DomUtil.create("div", "leaflet-bar");
      box.innerHTML = `<a href="#" id="btn-locate" title="My location" style="font-size:18px;width:40px;height:40px;line-height:40px">📍</a>
                       <a href="#" id="btn-campus" title="Back to campus" style="font-size:18px;width:40px;height:40px;line-height:40px">🏫</a>`;
      L.DomEvent.disableClickPropagation(box);
      box.querySelector("#btn-locate").onclick = (e) => { e.preventDefault(); goToMe(); };
      box.querySelector("#btn-campus").onclick = (e) => { e.preventDefault(); map.flyTo(CAMPUS, 16); };
      return box;
    },
  });
  map.addControl(new Ctl());
  map.on("click", (e) => {
    if (!pickMode) return;
    pickMode = false;
    setFixSpot(e.latlng.lat, e.latlng.lng, "Spot you tapped on the map");
    $("fix").classList.remove("hidden");
  });
}

// ---------- fix a wrong pin ----------
window.openFix = (placeId) => {
  const p = places.find((x) => x.id === placeId);
  if (!p) return;
  map.closePopup();
  fix = { placeId, lat: null, lng: null, label: "" };
  $("fix-place").textContent = `${CAT_ICON[p.category] || ""} ${p.name}`;
  $("fix-search").value = "";
  $("fix-results").innerHTML = "";
  $("fix-note").value = p.room_note || "";
  $("fix-chosen").textContent = "No spot chosen yet.";
  $("fix-submit").disabled = true;
  $("fix").classList.remove("hidden");
};

function setFixSpot(lat, lng, label) {
  fix.lat = lat; fix.lng = lng; fix.label = label;
  $("fix-chosen").innerHTML = `<b>${esc(label)}</b><br><span class="text-xs">${lat.toFixed(5)}, ${lng.toFixed(5)} · drag the red pin on the map to fine-tune</span>`;
  $("fix-submit").disabled = false;
  if (fixMarker) fixMarker.setLatLng([lat, lng]);
  else {
    fixMarker = L.marker([lat, lng], { draggable: true, zIndexOffset: 1000 }).addTo(map).bindTooltip("Suggested spot");
    fixMarker.on("dragend", () => { const ll = fixMarker.getLatLng(); setFixSpot(ll.lat, ll.lng, "Spot you dragged to"); });
  }
}

function closeFix() {
  $("fix").classList.add("hidden");
  pickMode = false;
  if (fixMarker) { fixMarker.remove(); fixMarker = null; }
  fix = null;
}

async function searchBuilding(ev) {
  ev.preventDefault();
  const q = $("fix-search").value.trim();
  if (!q) return;
  $("fix-results").innerHTML = `<div class="text-xs text-stone-500">Searching…</div>`;
  // OpenStreetMap search, limited to the CMU campus box. Only runs on Enter / button (usage policy).
  const url = "https://nominatim.openstreetmap.org/search?format=json&limit=6&bounded=1"
    + "&viewbox=-79.9600,40.4530,-79.9300,40.4330&q=" + encodeURIComponent(q);
  try {
    const res = await fetch(url, { headers: { "Accept-Language": "en" } });
    const rows = await res.json();
    if (!rows.length) {
      $("fix-results").innerHTML = `<div class="text-xs text-stone-500">No building found on campus. Try another name, or tap on the map.</div>`;
      return;
    }
    $("fix-results").innerHTML = rows.map((r, i) =>
      `<button data-i="${i}" class="res w-full text-left rounded-lg border border-stone-200 px-3 py-2 text-sm hover:bg-stone-50">${esc(r.display_name.split(",").slice(0, 2).join(","))}</button>`).join("");
    document.querySelectorAll(".res").forEach((b) => (b.onclick = () => {
      const r = rows[+b.dataset.i];
      setFixSpot(+r.lat, +r.lon, r.display_name.split(",")[0]);
      map.setView([+r.lat, +r.lon], 18);
      $("fix-results").innerHTML = "";
    }));
  } catch {
    $("fix-results").innerHTML = `<div class="text-xs text-red-700">Search is unavailable right now. Tap on the map instead.</div>`;
  }
}

async function submitFix() {
  if (!fix || fix.lat == null) return;
  $("fix-submit").disabled = true;
  try {
    const d = await api(`/api/places/${fix.placeId}/fix`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: user.id, lat: fix.lat, lng: fix.lng, note: $("fix-note").value }),
    });
    setBalance(d.credits);
    toast(d.message, d.status === "moved" ? "good" : "info");
    const id = fix.placeId;
    closeFix();
    await loadPlaces();
    if (d.status === "moved" && markers[id]) { markers[id].setLatLng([d.lat, d.lng]); map.flyTo([d.lat, d.lng], 18); }
  } catch (e) {
    toast(e.message, "bad");
    $("fix-submit").disabled = false;
  }
}

function showMe() {
  if (!myPos || !map) return;
  const ll = [myPos.lat, myPos.lng];
  if (myDot) myDot.setLatLng(ll);
  else myDot = L.circleMarker(ll, { radius: 8, color: "white", weight: 3, fillColor: "#2563eb", fillOpacity: 1 })
    .addTo(map).bindTooltip("You are here");
}

function goToMe() {
  if (myPos) { showMe(); map.flyTo([myPos.lat, myPos.lng], 18); return; }
  if (!navigator.geolocation) return toast("Location is not available on this device", "bad");
  toast("Finding you…");
  navigator.geolocation.getCurrentPosition(
    (pos) => { myPos = { lat: pos.coords.latitude, lng: pos.coords.longitude }; showMe(); map.flyTo([myPos.lat, myPos.lng], 18); },
    () => toast("Allow location access in your browser to use this", "bad"),
    { enableHighAccuracy: true, timeout: 10000 }
  );
}

function renderFilters() {
  const cats = ["all", "study", "dining", "lounge", "laundry", "gym"];
  $("filters").innerHTML = cats.map((c) => {
    const on = c === activeCat;
    const label = c === "all" ? "All" : `${CAT_ICON[c]} ${CAT_LABEL[c]}`;
    return `<button data-cat="${c}" class="flt rounded-full px-3 py-1 text-xs font-medium ${on ? "bg-stone-900 text-white" : "bg-white border border-stone-300"}">${label}</button>`;
  }).join("");
  document.querySelectorAll(".flt").forEach((b) => (b.onclick = () => { activeCat = b.dataset.cat; renderFilters(); applyFilter(); }));
}

function applyFilter() {
  for (const p of places) {
    const m = markers[p.id];
    if (!m) continue;
    const show = activeCat === "all" || p.category === activeCat;
    if (show && !map.hasLayer(m)) m.addTo(map);
    if (!show && map.hasLayer(m)) m.remove();
  }
}

function pinHtml(p) {
  const color = p.fresh && p.level ? LEVEL_COLOR[p.level] : "#d6d3d1";
  return `<div style="position:relative">
    <div class="pin" style="background:${color}">${CAT_ICON[p.category] || "📍"}</div>
    <div class="bounty">🪙${p.bounty}</div></div>`;
}

function popupHtml(p) {
  const status = p.fresh && p.level
    ? `<span style="color:${LEVEL_COLOR[p.level]};font-weight:700">${LEVEL_LABEL[p.level]}</span>`
      + ` <span style="color:#78716c">(${p.n_reports} photo${p.n_reports > 1 ? "s" : ""})</span>`
    : `<span style="color:#78716c">Unknown right now</span>`;
  return `<div style="min-width:200px">
    <div style="font-weight:700;font-size:14px">${esc(p.name)}</div>
    <div style="margin:2px 0">${status} · <span style="color:#78716c">${freshnessText(p)}</span></div>
    ${p.room_note ? `<div style="font-size:12px">📌 ${esc(p.room_note)}</div>` : ""}
    <div style="color:#78716c;font-size:12px">Snap it now for 🪙 ${p.bounty}</div>
    <div id="detail-${p.id}" style="margin-top:6px"></div>
    <button onclick="openPlace('${p.id}')" style="margin-top:6px;width:100%;background:#b91c1c;color:white;border-radius:8px;padding:6px;font-weight:600">
      ${p.fresh ? "See details &amp; photos · 🪙1" : "Request a report (free)"}</button>
    <button onclick="openFix('${p.id}')" style="margin-top:6px;width:100%;color:#57534e;font-size:12px;text-decoration:underline">
      📍 Wrong location?${p.pending_fixes ? ` (${p.pending_fixes} waiting for a 2nd person)` : ""}${p.corrected ? " · ✅ fixed by students" : ""}</button>
  </div>`;
}

async function loadPlaces() {
  places = await api("/api/places");
  for (const p of places) {
    const icon = L.divIcon({ html: pinHtml(p), iconSize: [34, 34], iconAnchor: [17, 17] });
    if (markers[p.id]) {
      markers[p.id].setIcon(icon);
      markers[p.id].setLatLng([p.lat, p.lng]);
      if (!markers[p.id].isPopupOpen()) markers[p.id].setPopupContent(popupHtml(p));
    } else {
      markers[p.id] = L.marker([p.lat, p.lng], { icon }).addTo(map).bindPopup(popupHtml(p));
    }
  }
  applyFilter();
  const sel = $("snap-place");
  if (sel.options.length <= 1) {
    for (const cat of Object.keys(CAT_LABEL)) {
      const group = document.createElement("optgroup");
      group.label = `${CAT_ICON[cat]} ${CAT_LABEL[cat]}`;
      places.filter((p) => p.category === cat).forEach((p) => group.appendChild(new Option(p.name, p.id)));
      if (group.children.length) sel.appendChild(group);
    }
  }
}

function levelBadge(level, extra = "") {
  if (!level) return `<span class="text-stone-500">Unknown</span>`;
  return `<span class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-sm font-semibold text-white" style="background:${LEVEL_COLOR[level]}">${LEVEL_LABEL[level]}${extra}</span>`;
}

function seatsText(o) {
  if (o.total) return `${o.occupied}/${o.total} seats taken (${Math.round((o.occupied / o.total) * 100)}%)`;
  if (o.line_length != null) return `${o.line_length} people in line`;
  return "";
}

function photoSrc(reportId) {
  return `/api/reports/${reportId}/photo?user_id=${encodeURIComponent(user.id)}`;
}

// Bottom sheet: AI summary of the whole area + every blurred photo it is based on.
window.openPlace = async (placeId) => {
  try {
    const d = await api(`/api/places/${placeId}?user_id=${encodeURIComponent(user.id)}`);
    setBalance(d.credits);
    if (!d.summary) {
      const box = $(`detail-${placeId}`);
      if (box) box.innerHTML = `<div style="font-size:12px;color:#78716c">Request sent. The bounty for this place just went up.</div>`;
      loadPlaces();
      return;
    }
    const sm = d.summary;
    const reports = d.reports.map((r) => `
      <div class="rounded-xl border border-stone-200 overflow-hidden">
        ${r.has_photo ? `<img src="${photoSrc(r.id)}" class="w-full" alt="blurred photo" loading="lazy" />` : ""}
        <div class="p-2 text-xs flex flex-wrap items-center gap-2">
          ${levelBadge(r.level)}<span>${esc(seatsText(r))}</span>
          <span class="ml-auto text-stone-500">${r.minutes_old} min ago</span>
        </div>
        ${r.summary ? `<div class="px-2 pb-2 text-xs text-stone-600">${esc(r.summary)}</div>` : ""}
      </div>`).join("");
    $("sheet-body").innerHTML = `
      <div class="flex items-start gap-2">
        <span class="text-2xl">${CAT_ICON[d.category] || "📍"}</span>
        <div><div class="font-bold text-lg leading-tight">${esc(d.name)}</div>
        <div class="text-xs text-stone-500">${esc(d.description || "")}</div>
        ${d.room_note ? `<div class="text-xs">📌 ${esc(d.room_note)}</div>` : ""}</div>
      </div>
      <div class="mt-3 rounded-xl bg-stone-50 p-3">
        <div class="text-xs font-semibold text-stone-500 uppercase tracking-wide">AI summary of this area</div>
        <div class="mt-1 flex flex-wrap items-center gap-2">${levelBadge(sm.level)}<span class="text-sm">${esc(seatsText(sm))}</span></div>
        <div class="mt-1 text-xs text-stone-500">Based on ${sm.n_reports} photo${sm.n_reports > 1 ? "s" : ""} from the last ${d.stale_after} min${d.charged ? ` · charged 🪙${d.charged}` : " · already unlocked"}</div>
        ${stateHtml(sm)}
      </div>
      <div class="mt-3 text-xs text-stone-500">Photos behind this summary. Faces are blurred, people are covered in gray, green boxes are free seats. Photos disappear when they go stale.</div>
      <div class="mt-2 space-y-3">${reports}</div>`;
    $("sheet").classList.remove("hidden");
  } catch (e) {
    toast(e.message, "bad");
  }
};

function stateHtml(state) {
  if (!state) return "";
  const rows = [];
  const add = (k, v) => rows.push(`<div style="display:flex;justify-content:space-between;gap:8px"><span style="color:#78716c">${k}</span><b>${esc(v)}</b></div>`);
  const text = state.latest_summary || state.summary;
  if (text) rows.push(`<div style="margin-bottom:4px">${esc(text)}</div>`);
  if (state.total_seats != null) add("Seats taken", `${state.occupied_seats ?? "?"}/${state.total_seats}`);
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
      const color = r.fresh && r.level ? LEVEL_COLOR[r.level] : "#a8a29e";
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
    if (d.needs_choice) {  // several places nearby: let the user pick, keep the same photo
      $("snap-steps").innerHTML = "";
      $("snap-result").innerHTML = `<div class="fade-in rounded-xl p-3 bg-amber-50 text-amber-900">
        <div class="font-semibold">Which place is this?</div>
        <div class="mt-2 flex flex-wrap gap-2">${d.options.map((o) =>
          `<button class="pick rounded-full bg-white border border-amber-300 px-3 py-1 text-sm" data-id="${o.id}">${CAT_ICON[o.category] || ""} ${esc(o.name)}</button>`).join("")}</div></div>`;
      document.querySelectorAll(".pick").forEach((b) => (b.onclick = () => { $("snap-place").value = b.dataset.id; upload(); }));
      $("snap-btn").disabled = false;
      return;
    }
    s.forEach((el) => (el.firstChild.textContent = "✅"));
    step(d.accepted ? "Original deleted. Only the blurred, covered version is kept (until it goes stale)." : "Photo deleted.", true);
    setBalance(d.credits);
    $("snap-result").innerHTML = `<div class="fade-in rounded-xl p-3 ${d.accepted ? "bg-green-50 text-green-900" : "bg-red-50 text-red-900"}">
      <div class="font-semibold">${esc(d.message)}</div>
      ${d.summary ? `<div class="text-sm mt-1">AI saw: ${esc(d.summary)}</div>` : ""}
      ${d.mock ? `<div class="text-xs mt-1 opacity-70">Mock AI mode (no OPENROUTER_KEY).</div>` : ""}
      ${d.accepted && d.report ? `<div class="mt-2 flex flex-wrap items-center gap-2 text-sm"><span>This photo:</span>${levelBadge(d.report.level)}<span>${esc(seatsText(d.report))}</span></div>` : ""}
      ${d.accepted && d.area ? `<div class="mt-1 flex flex-wrap items-center gap-2 text-sm"><span>Whole area now:</span>${levelBadge(d.area.level)}<span class="text-xs opacity-70">from ${d.area.n_reports} photo${d.area.n_reports > 1 ? "s" : ""}</span></div>` : ""}
      ${d.photo_url ? `<img src="${d.photo_url}?user_id=${encodeURIComponent(user.id)}" class="mt-2 w-full rounded-lg" alt="what other students will see" />
        <div class="text-xs mt-1 opacity-70">What other students see: ${d.faces_blurred} face(s) blurred, ${d.people_covered} person(s) covered, green = free seat.</div>` : ""}
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
    (pos) => { myPos = { lat: pos.coords.latitude, lng: pos.coords.longitude }; showMe(); },
    () => {}, { enableHighAccuracy: true, timeout: 8000 }
  );
  // keep the blue dot following the user
  navigator.geolocation.watchPosition(
    (pos) => { myPos = { lat: pos.coords.latitude, lng: pos.coords.longitude }; showMe(); },
    () => {}, { enableHighAccuracy: true, maximumAge: 15000 }
  );
}

document.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
  $("account-btn").onclick = openAccount;
  $("modal-close").onclick = () => $("modal").classList.add("hidden");
  $("sheet-close").onclick = () => $("sheet").classList.add("hidden");
  $("fix-search-form").onsubmit = searchBuilding;
  $("fix-cancel").onclick = closeFix;
  $("fix-submit").onclick = submitFix;
  $("fix-tap").onclick = () => { pickMode = true; $("fix").classList.add("hidden"); showTab("map"); toast("Tap the correct spot on the map"); };
  $("fix-here").onclick = () => {
    if (myPos) return setFixSpot(myPos.lat, myPos.lng, "Your current location");
    navigator.geolocation?.getCurrentPosition(
      (pos) => { myPos = { lat: pos.coords.latitude, lng: pos.coords.longitude }; setFixSpot(myPos.lat, myPos.lng, "Your current location"); },
      () => toast("Allow location access to use this", "bad"), { enableHighAccuracy: true, timeout: 10000 });
  };
  $("ask-btn").onclick = ask;
  $("snap-file").onchange = onFile;
  $("snap-btn").onclick = upload;
  renderExamples();
  initMap();
  renderFilters();
  showTab("map");
  locate();
  loadStatus();
  await ensureUser();
  await loadPlaces();
  setInterval(loadPlaces, 15000);  // live updates
});
