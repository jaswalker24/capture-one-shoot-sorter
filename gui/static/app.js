let selectedFolder = null;
let schedFolder = null;
let passState = [];
let dragId = null;
let presetsCache = [];
let activeSchedulePath = null;

async function api(path, opts = {}) {
  const r = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function $(id) { return document.getElementById(id); }
function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function checkLogin() {
  const s = await api("/api/login_status");
  if (s.ok) showApp();
}
async function doLogin() {
  $("loginErr").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify({ token: $("tokenInput").value }) });
    showApp();
  } catch (e) {
    $("loginErr").textContent = e.message || "Login failed";
  }
}

function setupTabs() {
  document.querySelectorAll("#tabs button").forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll("#tabs button").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".panel-section").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      $("tab-" + btn.dataset.tab).classList.add("active");
      if (btn.dataset.tab === "schedule") loadScheduleTab();
      if (btn.dataset.tab === "history") renderHistory();
      if (btn.dataset.tab === "profiles") renderProfiles();
      if (btn.dataset.tab === "settings") loadNotify();
    };
  });
}

async function showApp() {
  $("loginView").classList.add("hidden");
  $("appView").classList.remove("hidden");
  setupTabs();
  const passes = await api("/api/passes");
  const order = passes.default_order;
  const byId = Object.fromEntries(passes.passes.map(p => [p.id, p]));
  passState = order.map(id => ({ ...byId[id], checked: true }));
  for (const p of passes.passes) {
    if (!passState.find(x => x.id === p.id)) passState.push({ ...p, checked: false });
  }
  renderPasses();
  const years = await api("/api/years");
  $("year").innerHTML = years.years.map(y => `<option value="${y}">${y}</option>`).join("");
  $("schedYear").innerHTML = $("year").innerHTML;
  if (years.years.length) {
    await loadFolders(years.years[0], "folders", "folderPath", v => selectedFolder = v);
    await loadFolders(years.years[0], "schedFolders", "schedFolderPath", v => schedFolder = v);
  }
  $("year").onchange = () => loadFolders($("year").value, "folders", "folderPath", v => selectedFolder = v);
  $("schedYear").onchange = () => loadFolders($("schedYear").value, "schedFolders", "schedFolderPath", v => schedFolder = v);
  const ex = await api("/api/exports");
  $("exportFolder").innerHTML = (ex.folders || []).map(f => `<option value="${f.path}">${f.name}</option>`).join("");
  await loadPresetsIntoSelect();
  wireProfileForm();
  refresh();
  setInterval(refresh, 3000);
}

async function loadFolders(year, listId, pathId, setter) {
  const data = await api("/api/folders?year=" + encodeURIComponent(year));
  const box = $(listId);
  box.innerHTML = "";
  for (const f of data.folders) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = f.images == null ? f.name : `${f.name}  (${f.images} images)`;
    b.onclick = () => {
      setter(f.path);
      $(pathId).textContent = f.path;
      [...box.querySelectorAll("button")].forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      if (listId === "folders" && /BrandsHatch/i.test(f.name) && !$("context").value) {
        $("context").value = "Britcar Into The Night, Brands Hatch";
        $("profile").value = "motorsport";
      }
    };
    box.appendChild(b);
  }
}

function renderPasses() {
  const ul = $("passes");
  ul.innerHTML = "";
  passState.forEach((p, idx) => {
    const li = document.createElement("li");
    li.draggable = true;
    li.dataset.id = p.id;
    li.innerHTML = `
      <span class="handle">☰</span>
      <span class="arrows">
        <button type="button" data-up>▲</button>
        <button type="button" data-down>▼</button>
      </span>
      <span>
        <label class="inline" style="margin:0">
          <input type="checkbox" ${p.checked ? "checked" : ""} />
          <strong>${p.label}</strong>
          <span class="badge">${p.id}</span>
        </label>
        ${p.note ? `<div class="note">${p.note}</div>` : ""}
      </span><span></span>`;
    li.querySelector("input").onchange = e => { p.checked = e.target.checked; };
    li.querySelector("[data-up]").onclick = () => movePass(idx, -1);
    li.querySelector("[data-down]").onclick = () => movePass(idx, 1);
    li.addEventListener("dragstart", () => { dragId = p.id; li.classList.add("dragging"); });
    li.addEventListener("dragend", () => { dragId = null; li.classList.remove("dragging"); });
    li.addEventListener("dragover", e => e.preventDefault());
    li.addEventListener("drop", e => {
      e.preventDefault();
      if (!dragId || dragId === p.id) return;
      const from = passState.findIndex(x => x.id === dragId);
      const to = passState.findIndex(x => x.id === p.id);
      const [item] = passState.splice(from, 1);
      passState.splice(to, 0, item);
      renderPasses();
    });
    ul.appendChild(li);
  });
}
function movePass(idx, delta) {
  const j = idx + delta;
  if (j < 0 || j >= passState.length) return;
  const [item] = passState.splice(idx, 1);
  passState.splice(j, 0, item);
  renderPasses();
}

async function addJob() {
  if (!selectedFolder) return alert("Pick a folder");
  const passes = passState.filter(p => p.checked).map(p => p.id);
  if (!passes.length) return alert("Tick at least one pass");
  if (passes.includes("captions")) return alert("Captions belong in Exports only");
  const body = {
    folder: selectedFolder, profile: $("profile").value, context: $("context").value, passes,
    apply_each: $("applyEach").checked, dry_run: $("dryRun").checked,
    ocr_missing_car_number: $("ocrMissing").checked, apply_rank_keywords: $("rankKw").checked,
    afpoints: $("afPoints").checked, afpoints_annotate: Number($("afAnnotate").value || 0),
    sample: Number($("sample").value || 0),
  };
  if ($("presetSelect").dataset.profileId) body.profile_id = $("presetSelect").dataset.profileId;
  if ($("presetSelect").dataset.schedulePath) body.schedule_path = $("presetSelect").dataset.schedulePath;
  await api("/api/queue/add", { method: "POST", body: JSON.stringify(body) });
  refresh();
}

function fmtProgress(j) {
  const p = j.progress || {};
  if (!p.total) return j.current_pass ? `Running ${j.current_pass}` : (j.status || "");
  return `${p.pass || j.current_pass}: ${p.n}/${p.total} (${Math.round(100 * p.n / p.total)}%)`;
}

function renderQueue(data) {
  const box = $("queue");
  const active = (data.jobs || []).filter(j => ["queued", "running"].includes(j.status));
  const recent = (data.jobs || []).filter(j => ["done", "error", "cancelled"].includes(j.status)).slice(-5).reverse();
  const list = [...active, ...recent];
  if (!list.length) {
    box.innerHTML = `<p class="note">${data.paused ? "Queue paused. " : ""}No jobs yet.</p>`;
    return;
  }
  box.innerHTML = list.map(j => {
    const pct = j.progress && j.progress.total ? Math.round(100 * j.progress.n / j.progress.total) : (j.status === "done" ? 100 : 0);
    const log = (j.log_tail || []).slice(-12).join("\n");
    return `<div class="job">
      <div class="top"><div>
        <div class="title">${escapeHtml(j.folder_name || j.folder)} <span class="badge ${j.status}">${j.status}</span></div>
        <div class="meta">${(j.passes || []).join(" → ")} · ${j.profile}${j.dry_run ? " · dry-run" : ""}</div>
        <div class="meta">${fmtProgress(j)}</div>
      </div>
      <div>${j.status === "queued" || j.status === "running" ? `<button class="danger" data-cancel="${j.id}">Cancel</button>` : ""}</div></div>
      <div class="bar"><span style="width:${pct}%"></span></div>
      <div class="log">${escapeHtml(log)}</div></div>`;
  }).join("");
  box.querySelectorAll("[data-cancel]").forEach(b => {
    b.onclick = async () => { await api("/api/queue/cancel", { method: "POST", body: JSON.stringify({ id: b.dataset.cancel }) }); refresh(); };
  });
}

async function renderHistory() {
  const data = await api("/api/history");
  const box = $("history");
  if (!data.history.length) { box.innerHTML = `<p class="note">No history yet.</p>`; return; }
  box.innerHTML = data.history.slice(0, 30).map(j => {
    const kc = j.keyword_count != null ? j.keyword_count : sumAdded(j.added_keywords);
    const v = j.verify ? ` · ratings/colours changed: ${j.verify.rating_or_colour_changed}` : "";
    const undo = j.undo ? ` · undone ${j.undo.when} (−${j.undo.removed})` : "";
    return `<div class="job">
      <div class="title">${escapeHtml(j.folder_name || (j.folder || "").split("/").pop() || j.id)}
        <span class="badge ${j.status}">${j.status}</span></div>
      <div class="meta">${escapeHtml(j.finished || j.created || "")} · ${(j.passes || []).join(", ")}
        · keywords ${kc} · variants ${j.variant_count || Object.keys(j.added_keywords || {}).length}${v}${undo}
        ${j.error ? " · " + escapeHtml(j.error) : ""}</div>
      <div class="row" style="margin-top:8px">
        <button class="secondary" data-undoprev="${j.id}">Preview undo</button>
        <button class="danger" data-undo="${j.id}">Remove this run's keywords</button>
      </div>
      <div class="note" id="undo-${j.id}"></div>
    </div>`;
  }).join("");
  box.querySelectorAll("[data-undoprev]").forEach(b => {
    b.onclick = async () => {
      const r = await api("/api/history/undo_preview", { method: "POST", body: JSON.stringify({ id: b.dataset.undoprev }) });
      $(`undo-${b.dataset.undoprev}`).textContent =
        `Would remove ${r.plan.keyword_count} keywords on ${r.plan.variant_count} variants` +
        (r.plan.sources?.length ? ` (from ${r.plan.sources.length} apply result file(s))` : " — none found");
    };
  });
  box.querySelectorAll("[data-undo]").forEach(b => {
    b.onclick = async () => {
      const id = b.dataset.undo;
      const prev = await api("/api/history/undo_preview", { method: "POST", body: JSON.stringify({ id }) });
      if (!prev.plan.keyword_count) return alert("No keywords recorded for this run");
      if (!confirm(`Dry-run says ${prev.plan.keyword_count} keywords on ${prev.plan.variant_count} variants.\nRemove them now? Stars/colours will not change.`)) return;
      const dry = await api("/api/history/undo", { method: "POST", body: JSON.stringify({ id, apply: false }) });
      if (!dry.ok) return alert(dry.error || "undo dry-run failed");
      if (!confirm(`Confirm remove ${dry.removed} keywords (skipped ${dry.skipped}, failures ${(dry.failures||[]).length})?`)) return;
      const res = await api("/api/history/undo", { method: "POST", body: JSON.stringify({ id, apply: true, confirm: true }) });
      alert(res.ok ? `Removed ${res.removed}` : (res.error || "undo failed"));
      renderHistory();
    };
  });
}
function sumAdded(ak) { return Object.values(ak || {}).reduce((n, a) => n + (a?.length || 0), 0); }

async function renderStats() {
  const s = await api("/api/stats");
  const bits = [];
  if (s.cpu_idle_pct != null) bits.push(`<span class="pill"><strong>${(100 - s.cpu_idle_pct).toFixed(0)}%</strong> CPU</span>`);
  if (s.mem_used_gb != null) bits.push(`<span class="pill"><strong>${s.mem_used_gb}/${s.mem_total_gb} GB</strong> mem</span>`);
  if (s.mem_compressor_gb) bits.push(`<span class="pill warn"><strong>${s.mem_compressor_gb} GB</strong> compressed</span>`);
  bits.push(s.nas_mounted ? `<span class="pill ok">NAS ok</span>` : `<span class="pill bad">NAS missing</span>`);
  if (s.disk_home_free_gb != null) bits.push(`<span class="pill"><strong>${s.disk_home_free_gb} GB</strong> free M5</span>`);
  if (s.disk_nas_free_gb != null) bits.push(`<span class="pill"><strong>${s.disk_nas_free_gb} GB</strong> free NAS</span>`);
  bits.push(s.capture_one_running ? `<span class="pill ok">Capture One</span>` : `<span class="pill warn">C1 not running</span>`);
  bits.push(s.caption_model_loaded
    ? `<span class="pill bad">caption model LOADED</span>`
    : `<span class="pill ok">caption model off</span>`);
  if ((s.ollama_models || []).length) bits.push(`<span class="pill">ollama: ${escapeHtml((s.ollama_models||[]).join(", "))}</span>`);
  else bits.push(`<span class="pill">ollama idle</span>`);
  if (s.thermal) bits.push(`<span class="pill" title="${escapeHtml(s.thermal)}">therm</span>`);
  if (s.memory_pressure_line) bits.push(`<span class="pill warn" title="${escapeHtml(s.memory_pressure_line)}">pressure</span>`);
  if (s.fullrun_running) bits.push(`<span class="pill warn">fullrun.py</span>`);
  if (s.paused) bits.push(`<span class="pill warn">queue paused</span>`);
  if (s.notify?.enabled) bits.push(`<span class="pill ok">notify on</span>`);
  $("stats").innerHTML = bits.join("");
}

async function refresh() {
  try {
    const q = await api("/api/queue");
    renderQueue(q);
    await renderStats();
    if ($("tab-history").classList.contains("active")) await renderHistory();
  } catch (e) {
    if (String(e.message).includes("login")) {
      $("appView").classList.add("hidden");
      $("loginView").classList.remove("hidden");
    }
  }
}

/* ---- schedule ---- */
async function loadScheduleTab() {
  const data = await api("/api/schedule/list");
  $("schedFile").innerHTML = (data.schedules || []).map(s =>
    `<option value="${escapeHtml(s.path)}">${escapeHtml(s.name)} — ${s.confirmed}/${s.events} confirmed</option>`).join("");
  if (data.schedules?.length && !activeSchedulePath) activeSchedulePath = data.schedules[0].path;
}
function renderSchedPreview(preview, path) {
  activeSchedulePath = path || activeSchedulePath;
  const box = $("schedPreview");
  const warns = (preview.warnings || []).map(w => `<li>${escapeHtml(w)}</li>`).join("");
  const rows = (preview.rows || []).slice(0, 80).map(r =>
    `<tr><td>${escapeHtml(r.date)}</td><td>${escapeHtml(r.event)}</td><td>${escapeHtml(r.venue)}</td>
     <td>${escapeHtml(r.status)}</td><td>${escapeHtml(r.slot_type)}</td><td>${escapeHtml(r.act)}</td>
     <td>${escapeHtml(r.start)}</td><td>${escapeHtml(r.end)}</td><td>${escapeHtml(r.note)}</td></tr>`).join("");
  box.innerHTML = `
    <p class="note">${escapeHtml(preview.name || "")} · tz ${escapeHtml(preview.timezone)} · buffer ${preview.changeover_buffer_min}m
      · ${preview.events_confirmed}/${preview.events_total} confirmed</p>
    ${warns ? `<ul class="warn-list">${warns}</ul>` : ""}
    <div style="max-height:280px;overflow:auto"><table class="data">
      <thead><tr><th>Date</th><th>Event</th><th>Venue</th><th>Status</th><th>Type</th><th>Act</th><th>Start</th><th>End</th><th>Note</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}
$("schedLoad").onclick = async () => {
  const path = $("schedFile").value;
  const p = await api("/api/schedule/preview?path=" + encodeURIComponent(path) + "&confirmed_only=" + ($("schedConfirmed").checked ? "1" : "0"));
  renderSchedPreview(p, path);
};
$("schedSave").onclick = async () => {
  const body = {
    filename: $("schedFilename").value || undefined,
    confirmed_only: $("schedConfirmed").checked,
    buffer_min: Number($("schedBuf").value || 3),
    timezone: "Asia/Tokyo",
  };
  if ($("schedCsv").value.trim()) body.csv = $("schedCsv").value;
  else if ($("schedJson").value.trim()) body.json = JSON.parse($("schedJson").value);
  else return alert("Paste JSON or CSV first");
  const r = await api("/api/schedule/save", { method: "POST", body: JSON.stringify(body) });
  activeSchedulePath = r.path;
  renderSchedPreview(r.preview, r.path);
  await loadScheduleTab();
  alert("Saved " + r.path);
};
$("schedDry").onclick = async () => {
  if (!schedFolder) return alert("Pick a folder");
  if (!activeSchedulePath) return alert("Load or save a schedule first");
  $("schedDryOut").textContent = "Running dry-run…";
  try {
    const r = await api("/api/schedule/dry_run", { method: "POST", body: JSON.stringify({
      folder: schedFolder, schedule_path: activeSchedulePath,
      camera_tz: $("schedCamTz").value, buffer_min: Number($("schedBuf").value || 3),
      confirmed_only: $("schedConfirmed").checked,
    })});
    const s = r.summary;
    $("schedDryOut").innerHTML = `<div class="note">Frames ${s.frames} · outside ${s.outside_slots} · changeover ${s.changeover}
      · cheki ${s.cheki} · meet ${s.meet} · needs_review ${s.needs_review}<br/>
      By event: ${escapeHtml(JSON.stringify(s.by_event))}<br/>
      By act: ${escapeHtml(JSON.stringify(s.by_act))}</div>
      <div class="log">${escapeHtml((s.sample_rows||[]).map(x => `${x.filename} ${x.local_time||""} ${x.event||"—" } ${(x.keywords||[]).join(",")}`).join("\n"))}</div>`;
  } catch (e) { $("schedDryOut").textContent = e.message; }
};
$("schedApply").onclick = async () => {
  if (!schedFolder) return alert("Pick a folder");
  if (!activeSchedulePath) return alert("Load or save a schedule first");
  if (!confirm("Queue schedule tagging? Additive keywords only; stars/colours untouched.")) return;
  const r = await api("/api/schedule/apply", { method: "POST", body: JSON.stringify({
    folder: schedFolder, schedule_path: activeSchedulePath,
    camera_tz: $("schedCamTz").value, buffer_min: Number($("schedBuf").value || 3),
    confirmed_only: $("schedConfirmed").checked, dry_run: false,
  })});
  alert("Queued " + r.job.id);
  document.querySelector('#tabs [data-tab="run"]').click();
  refresh();
};

/* ---- presets ---- */
let profilesCache = [];
let catalogsCache = {};
let editingProfileId = null;

async function loadPresetsIntoSelect() {
  const data = await api("/api/profiles");
  profilesCache = data.profiles || [];
  catalogsCache = data.catalogs || {};
  presetsCache = profilesCache; // back-compat for any old refs
  $("presetSelect").innerHTML = `<option value="">— custom —</option>` +
    profilesCache.filter(p => p.kind !== "exports").map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("");
  fillProfileFormCatalogs();
}
$("presetSelect").onchange = () => {
  const pr = profilesCache.find(p => p.id === $("presetSelect").value);
  if (!pr) return;
  applyProfileToRunForm(pr);
};

function applyProfileToRunForm(pr) {
  $("profile").value = pr.tagger_profile || pr.profile || "gig";
  $("applyEach").checked = pr.apply_each !== false;
  $("ocrMissing").checked = !!pr.ocr_missing_car_number;
  $("rankKw").checked = !!pr.apply_rank_keywords;
  const set = new Set(pr.passes || []);
  passState.forEach(p => { p.checked = set.has(p.id); });
  const ordered = [];
  for (const id of pr.passes || []) {
    const hit = passState.find(p => p.id === id);
    if (hit) ordered.push(hit);
  }
  for (const p of passState) if (!ordered.includes(p)) ordered.push(p);
  passState = ordered;
  renderPasses();
  if (pr.camera_tz) {
    // stash on addJob via dataset
    $("presetSelect").dataset.cameraTz = pr.camera_tz === "Other" ? (pr.camera_tz_other || "Europe/London") : pr.camera_tz;
    $("presetSelect").dataset.bufferMin = pr.buffer_min || 3;
    $("presetSelect").dataset.schedulePath = pr.schedule_path || "";
    $("presetSelect").dataset.profileId = pr.id || "";
    $("presetSelect").dataset.defaultKeywords = JSON.stringify(pr.default_keywords || []);
  }
}

function fillSelect(sel, items, mapLabel) {
  sel.innerHTML = items.map(x => {
    const v = typeof x === "string" ? x : x.value;
    const l = typeof x === "string" ? x : (mapLabel ? mapLabel(x) : x.label);
    return `<option value="${escapeHtml(v)}">${escapeHtml(l)}</option>`;
  }).join("");
}
function fillProfileFormCatalogs() {
  const c = catalogsCache;
  if (!c.shoot_types) return;
  fillSelect($("pfShoot"), c.shoot_types);
  fillSelect($("pfBundle"), Object.keys(c.pass_bundles || {}));
  fillSelect($("pfCamTz"), c.camera_timezones || []);
  fillSelect($("pfBuf"), (c.buffers || []).map(String));
  fillSelect($("pfStrict"), c.strictness || []);
  fillSelect($("pfNotify"), c.notify || []);
  fillSelect($("pfKwSet"), Object.keys(c.keyword_sets || {}));
}
async function loadSchedIntoProfileForm() {
  try {
    const data = await api("/api/schedule/list");
    $("pfSched").innerHTML = `<option value="">— none —</option>` +
      (data.schedules || []).map(s => `<option value="${escapeHtml(s.path)}">${escapeHtml(s.name)}</option>`).join("");
  } catch (e) {}
}
function syncKwPreview() {
  const set = $("pfKwSet").value;
  const base = (catalogsCache.keyword_sets || {})[set] || [];
  const extra = ($("pfExtraKw").value || "").split(",").map(s => s.trim()).filter(Boolean);
  $("pfKwPreview").textContent = "Default keywords: " + [...base, ...extra].join(", ") || "—";
}
async function applyShootTypeDefaults() {
  const st = $("pfShoot").value;
  const r = await api("/api/profiles/defaults", { method: "POST", body: JSON.stringify({ shoot_type: st }) });
  const pr = r.profile;
  $("pfBundle").value = pr.pass_bundle;
  $("pfTagger").value = pr.tagger_profile;
  $("pfCamTz").value = pr.camera_tz;
  $("pfBuf").value = String(pr.buffer_min);
  $("pfStrict").value = pr.strictness;
  $("pfNotify").value = pr.notify;
  $("pfKwSet").value = pr.keyword_set || st;
  $("pfPasses").value = (pr.passes || []).join(",");
  $("pfApplyEach").checked = pr.apply_each !== false;
  $("pfOcrMissing").checked = !!pr.ocr_missing_car_number;
  $("pfRankKw").checked = !!pr.apply_rank_keywords;
  $("pfCamOtherWrap").classList.toggle("hidden", $("pfCamTz").value !== "Other");
  syncKwPreview();
}
function readProfileForm() {
  const bundle = $("pfBundle").value;
  let passes = ($("pfPasses").value || "").split(",").map(s => s.trim()).filter(Boolean);
  if (bundle !== "Custom" && (catalogsCache.pass_bundles || {})[bundle]) {
    passes = catalogsCache.pass_bundles[bundle];
  }
  const ks = $("pfKwSet").value;
  const base = (catalogsCache.keyword_sets || {})[ks] || [];
  const extra = ($("pfExtraKw").value || "").split(",").map(s => s.trim()).filter(Boolean);
  const shoot = $("pfShoot").value;
  return {
    id: $("pfId").value || undefined,
    name: $("pfName").value || "Untitled",
    kind: shoot === "Exports only" ? "exports" : "catalog",
    shoot_type: shoot,
    tagger_profile: $("pfTagger").value,
    pass_bundle: bundle,
    passes,
    camera_tz: $("pfCamTz").value,
    camera_tz_other: $("pfCamOther").value,
    buffer_min: Number($("pfBuf").value || 3),
    keyword_set: ks,
    default_keywords: [...base, ...extra],
    schedule_path: $("pfSched").value,
    strictness: $("pfStrict").value,
    notify: $("pfNotify").value,
    apply_each: $("pfApplyEach").checked,
    ocr_missing_car_number: $("pfOcrMissing").checked,
    apply_rank_keywords: $("pfRankKw").checked,
  };
}
function fillProfileForm(pr) {
  editingProfileId = pr.id;
  $("profileFormTitle").textContent = "Edit profile";
  $("pfName").value = pr.name || "";
  $("pfId").value = pr.id || "";
  $("pfShoot").value = pr.shoot_type || "Gig";
  $("pfBundle").value = pr.pass_bundle || "Custom";
  $("pfTagger").value = pr.tagger_profile || "gig";
  $("pfCamTz").value = pr.camera_tz || "Europe/London";
  $("pfCamOther").value = pr.camera_tz_other || "";
  $("pfBuf").value = String(pr.buffer_min ?? 3);
  $("pfStrict").value = pr.strictness || "Normal";
  $("pfNotify").value = pr.notify || "Off";
  $("pfKwSet").value = pr.keyword_set || pr.shoot_type || "Gig";
  $("pfSched").value = pr.schedule_path || "";
  $("pfPasses").value = (pr.passes || []).join(",");
  $("pfApplyEach").checked = pr.apply_each !== false;
  $("pfOcrMissing").checked = !!pr.ocr_missing_car_number;
  $("pfRankKw").checked = !!pr.apply_rank_keywords;
  $("pfCamOtherWrap").classList.toggle("hidden", $("pfCamTz").value !== "Other");
  $("pfDup").disabled = !pr.id;
  syncKwPreview();
}
function clearProfileForm() {
  editingProfileId = null;
  $("profileFormTitle").textContent = "New profile";
  $("pfName").value = ""; $("pfId").value = "";
  $("pfDup").disabled = true;
  applyShootTypeDefaults();
}
async function renderProfiles() {
  await loadPresetsIntoSelect();
  await loadSchedIntoProfileForm();
  const box = $("profileList");
  box.innerHTML = profilesCache.map((p, i) => `
    <div class="preset-row">
      <div><strong>${escapeHtml(p.name)}</strong>
        <span class="badge">${escapeHtml(p.shoot_type || p.kind)}</span>
        <div class="meta">${escapeHtml((p.passes||[]).join(" → "))} · ${escapeHtml(p.camera_tz || "")} · buf ${p.buffer_min}m · notify ${escapeHtml(p.notify||"Off")}</div>
      </div>
      <div style="display:flex;gap:6px">
        <button class="secondary" data-up="${i}">▲</button>
        <button class="secondary" data-down="${i}">▼</button>
        <button class="secondary" data-edit="${p.id}">Edit</button>
        <button class="secondary" data-use="${p.id}">Use</button>
        <button class="danger" data-del="${p.id}">Delete</button>
      </div>
    </div>`).join("") || `<p class="note">No profiles yet.</p>`;
  box.querySelectorAll("[data-edit]").forEach(b => b.onclick = () => {
    const pr = profilesCache.find(x => x.id === b.dataset.edit); if (pr) fillProfileForm(pr);
  });
  box.querySelectorAll("[data-use]").forEach(b => b.onclick = () => {
    const pr = profilesCache.find(x => x.id === b.dataset.use); if (!pr) return;
    if (pr.kind === "exports") { document.querySelector('#tabs [data-tab="exports"]').click(); return; }
    $("presetSelect").value = pr.id; applyProfileToRunForm(pr);
    document.querySelector('#tabs [data-tab="run"]').click();
  });
  box.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    if (!confirm("Delete profile " + b.dataset.del + "?")) return;
    await api("/api/profiles/delete", { method: "POST", body: JSON.stringify({ id: b.dataset.del }) });
    renderProfiles();
  });
  box.querySelectorAll("[data-up], [data-down]").forEach(b => b.onclick = async () => {
    const i = Number(b.dataset.up ?? b.dataset.down);
    const j = b.dataset.up != null ? i - 1 : i + 1;
    if (j < 0 || j >= profilesCache.length) return;
    const order = profilesCache.map(p => p.id);
    [order[i], order[j]] = [order[j], order[i]];
    await api("/api/profiles/reorder", { method: "POST", body: JSON.stringify({ order }) });
    renderProfiles();
  });
  if (!editingProfileId) clearProfileForm();
}

// wire profile form once
document.addEventListener("DOMContentLoaded", () => {});
function wireProfileForm() {
  if ($("pfShoot").dataset.wired) return;
  $("pfShoot").dataset.wired = "1";
  $("pfShoot").onchange = () => applyShootTypeDefaults();
  $("pfBundle").onchange = () => {
    const b = $("pfBundle").value;
    const passes = (catalogsCache.pass_bundles || {})[b];
    if (passes && b !== "Custom") $("pfPasses").value = passes.join(",");
  };
  $("pfCamTz").onchange = () => $("pfCamOtherWrap").classList.toggle("hidden", $("pfCamTz").value !== "Other");
  $("pfKwSet").onchange = syncKwPreview;
  $("pfExtraKw").oninput = syncKwPreview;
  $("pfSave").onclick = async () => {
    try {
      const profile = readProfileForm();
      if (editingProfileId) profile.id = editingProfileId;
      await api("/api/profiles/save", { method: "POST", body: JSON.stringify({ profile }) });
      editingProfileId = null;
      await renderProfiles();
      alert("Saved");
    } catch (e) { alert(e.message); }
  };
  $("pfNew").onclick = () => clearProfileForm();
  $("pfDup").onclick = async () => {
    if (!editingProfileId) return;
    await api("/api/profiles/duplicate", { method: "POST", body: JSON.stringify({ id: editingProfileId }) });
    renderProfiles();
  };
}


/* ---- notify ---- */
async function loadNotify() {
  const s = await api("/api/notify/status");
  $("notifyEnabled").checked = !!s.enabled;
  $("notifyTg").checked = !!s.telegram_enabled;
  $("notifyEmail").checked = !!s.email_enabled;
  // configured != enabled channel; reload full via set response after save. Show notes.
  $("notifyStatus").innerHTML = `
    Telegram configured: <strong>${s.telegram_configured}</strong> ·
    Email configured: <strong>${s.email_configured}</strong><br/>
    ${(s.notes||[]).map(escapeHtml).join("<br/>")}<br/>
    Edit secrets in <code>${escapeHtml(s.config_path)}</code>`;
}
$("notifySave").onclick = async () => {
  const r = await api("/api/notify/set", { method: "POST", body: JSON.stringify({
    enabled: $("notifyEnabled").checked,
    telegram_enabled: $("notifyTg").checked,
    email_enabled: $("notifyEmail").checked,
  })});
  await loadNotify();
  alert("Saved toggles. Put bot_token/chat_id in notify.json for Telegram.");
};

/* ---- wires ---- */
$("loginBtn").onclick = doLogin;
$("tokenInput").addEventListener("keydown", e => { if (e.key === "Enter") doLogin(); });
$("addJob").onclick = addJob;
$("pauseBtn").onclick = () => api("/api/queue/pause", { method: "POST", body: "{}" }).then(refresh);
$("resumeBtn").onclick = () => api("/api/queue/resume", { method: "POST", body: "{}" }).then(refresh);
$("runCaptions").onclick = async () => {
  try {
    const r = await api("/api/exports/captions", { method: "POST", body: JSON.stringify({
      folder: $("exportFolder").value, profile: $("exportProfile").value,
      context: $("exportContext").value, sample: Number($("exportSample").value || 0),
    })});
    alert("Captions job started: " + r.job.id);
  } catch (e) { alert(e.message); }
};

async function start() {
  const u = new URL(location.href);
  const urlToken = u.searchParams.get("token");
  if (urlToken) {
    u.searchParams.delete("token");
    history.replaceState({}, "", u.pathname + u.search + u.hash);
    try { $("tokenInput").value = urlToken; await doLogin(); return; }
    catch (e) { $("loginErr").textContent = (e && e.message) || "Auto-login failed — paste token from ~/localai/gui/auth.token"; }
  }
  try { await checkLogin(); } catch (e) {}
}
start();
