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
      if (btn.dataset.tab === "training") loadTrainingTab();
      if (btn.dataset.tab === "profiles") renderProfiles();
      if (btn.dataset.tab === "settings") { loadNotify(); loadAutoqueue(); loadLocations(); }
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
  setupTrainingTab();
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
    preview_before_apply: $("previewFirst") ? $("previewFirst").checked : false,
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
  const active = (data.jobs || []).filter(j => ["awaiting_approval", "queued", "running"].includes(j.status));
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
        <div class="title">${escapeHtml(j.kind === "rank_train" ? "Rank training" : (j.folder_name || j.folder))} <span class="badge ${j.status}">${j.status}</span></div>
        <div class="meta">${j.kind === "rank_train"
          ? `exports · ${j.index_missing ? "index missing · " : ""}${j.auto_adopt ? "adopt if better" : "report only"}`
          : `${(j.passes || []).join(" → ")} · ${j.profile}${j.dry_run ? " · dry-run" : ""}`}</div>
        <div class="meta">${fmtProgress(j)}</div>
      </div>
      <div>${j.status === "queued" || j.status === "running" ? `<button class="danger" data-cancel="${j.id}">Cancel</button>` : ""}
        ${j.status === "awaiting_approval" ? `<button data-preview="${j.id}" data-shoot="${escapeHtml(j.folder_name || "")}">Preview &amp; approve</button>` : ""}</div></div>
      ${j.status === "awaiting_approval" && j.preview_summary ? `<div class="meta">Would add ${j.preview_summary.keywords_new} new keywords on ${j.preview_summary.frames_with_new} frames. Nothing written yet.</div>` : ""}
      <div class="bar"><span style="width:${pct}%"></span></div>
      <div class="log">${escapeHtml(log)}</div></div>`;
  }).join("");
  box.querySelectorAll("[data-preview]").forEach(b => {
    b.onclick = () => openPreview(b.dataset.shoot, b.dataset.preview, true);
  });
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
    if ($("tab-training") && $("tab-training").classList.contains("active")) await loadTrainingTab();
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


/* ---- locations (NAS photo roots) ---- */
let _locFields = [];

function locPreviewText(prev) {
  if (!prev) return "";
  if (prev.error) return prev.error;
  return prev.summary || `${prev.dirs || 0} folders, ${prev.files || 0} files at this level`;
}

function renderLocations() {
  const box = $("locFields");
  if (!box) return;
  box.innerHTML = _locFields.map(f => {
    const val = f.configured || f.resolved || "";
    const prev = f.preview || {};
    const ok = !!prev.readable;
    const env = f.env_override ? `<div class="loc-hint">Environment ${escapeHtml(f.env_name)} is set and still wins until you unset it.</div>` : "";
    const train = f.training_source ? `<div class="loc-train">
      <span class="badge-train">Training data</span>
      <label class="inline"><input type="checkbox" id="loc-train-${escapeHtml(f.key)}" ${f.training_enabled ? "checked" : ""} /> Use for the personal ranking model</label>
      <span class="note">${escapeHtml(f.training_role || "")}</span>
    </div>` : "";
    return `<div class="loc-row" data-key="${escapeHtml(f.key)}">
      <label for="loc-${escapeHtml(f.key)}">${escapeHtml(f.label)}</label>
      <input type="text" id="loc-${escapeHtml(f.key)}" value="${escapeHtml(val)}" placeholder="${f.optional ? "optional — leave blank to derive" : "paste a folder path"}" />
      <button type="button" class="secondary loc-preview-btn" data-key="${escapeHtml(f.key)}">Check</button>
      <button type="button" class="loc-browse-btn" data-key="${escapeHtml(f.key)}">Browse</button>
    </div>
    <div class="loc-hint">${escapeHtml(f.hint || "")}</div>
    ${train}
    ${env}
    <div class="loc-preview ${ok ? "ok" : (val ? "bad" : "")}" id="loc-prev-${escapeHtml(f.key)}">${escapeHtml(locPreviewText(prev))}</div>`;
  }).join("");
  box.querySelectorAll(".loc-browse-btn").forEach(btn => {
    btn.onclick = () => browseLocation(btn.dataset.key);
  });
  box.querySelectorAll(".loc-preview-btn").forEach(btn => {
    btn.onclick = () => previewLocation(btn.dataset.key);
  });
  box.querySelectorAll(".loc-row input").forEach(inp => {
    inp.addEventListener("change", () => previewLocation(inp.id.replace(/^loc-/, "")));
  });
}

async function loadLocations() {
  const s = $("locStatus");
  try {
    const data = await api("/api/locations");
    _locFields = data.fields || [];
    renderLocations();
    if (s) s.textContent = "";
  } catch (e) {
    if (s) s.textContent = e.message || "Could not load locations";
  }
}

async function previewLocation(key) {
  const inp = $("loc-" + key);
  const out = $("loc-prev-" + key);
  if (!inp || !out) return;
  const path = inp.value.trim();
  if (!path) {
    out.className = "loc-preview";
    out.textContent = "Leave blank to derive this path automatically.";
    return;
  }
  out.className = "loc-preview";
  out.textContent = "Checking…";
  try {
    const prev = await api("/api/locations/preview", { method: "POST", body: JSON.stringify({ path }) });
    out.className = "loc-preview " + (prev.readable ? "ok" : "bad");
    out.textContent = locPreviewText(prev);
  } catch (e) {
    out.className = "loc-preview bad";
    out.textContent = e.message || "Check failed";
  }
}

async function browseLocation(key) {
  const btn = document.querySelector(`.loc-browse-btn[data-key="${key}"]`);
  const status = $("locStatus");
  const inp = $("loc-" + key);
  const out = $("loc-prev-" + key);
  if (btn) btn.disabled = true;
  if (status) status.textContent = "A folder picker is on the Mac desktop — choose a folder or press Cancel.";
  try {
    const r = await api("/api/locations/browse", { method: "POST", body: JSON.stringify({ key }) });
    if (r.cancelled) {
      if (status) status.textContent = "Browse cancelled.";
      return;
    }
    if (!r.ok) {
      if (status) status.textContent = r.error || "Browse failed. Paste the path instead.";
      return;
    }
    if (inp) inp.value = r.path || "";
    if (out) {
      out.className = "loc-preview " + (r.preview && r.preview.readable ? "ok" : "bad");
      out.textContent = locPreviewText(r.preview);
    }
    if (status) status.textContent = "Path filled from the folder picker. Click Save locations to write config.json.";
  } catch (e) {
    if (status) status.textContent = e.message || "Browse failed. Paste the path instead.";
  } finally {
    if (btn) btn.disabled = false;
  }
}

if ($("locSave")) $("locSave").onclick = async () => {
  const status = $("locStatus");
  const paths = {};
  _locFields.forEach(f => {
    const inp = $("loc-" + f.key);
    paths[f.key] = inp ? inp.value.trim() : "";
  });
  $("locSave").disabled = true;
  if (status) status.textContent = "Saving…";
  try {
    const training_sources = {};
    _locFields.forEach(f => {
      if (!f.training_source) return;
      const box = $("loc-train-" + f.key);
      training_sources[f.key] = !!(box && box.checked);
    });
    const r = await api("/api/locations/save", { method: "POST", body: JSON.stringify({ paths, training_sources }) });
    _locFields = r.locations && r.locations.fields ? r.locations.fields : _locFields;
    renderLocations();
    const bak = r.backup ? ` Backup: ${r.backup.split("/").pop()}.` : "";
    if (status) status.textContent = (r.changed ? "Saved. The panel is using the new paths now." : "No changes to save.") + bak;
  } catch (e) {
    if (status) status.textContent = e.message || "Save failed";
  } finally {
    $("locSave").disabled = false;
  }
};

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

if ($("notifyTest")) $("notifyTest").onclick = async () => {
  $("notifyTest").disabled = true;
  try {
    const r = await api("/api/notify/test", { method: "POST", body: "{}" });
    alert(r.ok ? "Test message sent. Check Telegram." : "Not sent: " + r.error);
  } catch (e) { alert("Not sent: " + e.message); }
  finally { $("notifyTest").disabled = false; }
};

/* ---- auto-queue new shoots ---- */
async function loadAutoqueue() {
  if (!$("aqEnabled")) return;
  let s;
  try { s = await api("/api/autoqueue/status"); } catch (e) { $("aqStatus").textContent = "Auto-queue not available yet (panel restart pending)."; return; }
  $("aqEnabled").checked = !!s.enabled;
  $("aqPreview").checked = !!s.preview_before_apply;
  $("aqProfile").innerHTML = (s.profiles || []).map(p =>
    `<option value="${escapeHtml(p.id)}" ${p.id === s.profile_id ? "selected" : ""}>${escapeHtml(p.name || p.id)}</option>`).join("");
  if ($("aqCutoff") && s.cutoff) $("aqCutoff").value = String(s.cutoff).slice(0, 16);
  const waiting = Object.entries(s.waiting || {}).map(([k, v]) => `${escapeHtml(k)}: ${escapeHtml(v.why_waiting || "waiting")} (${v.sig ? v.sig.n : 0} images)`);
  const queued = Object.entries(s.queued || {}).slice(-5).map(([k, v]) => `${escapeHtml(k)} → ${escapeHtml(v)}`);
  $("aqStatus").innerHTML = `${s.enabled ? "On" : "Off"}${s.nas_mounted ? "" : " · NAS not mounted"}` +
    `${s.cutoff ? ` · last-run cutoff ${escapeHtml(s.cutoff)} ${escapeHtml(s.cutoff_tz || "Europe/London")}` : ""}` +
    `${s.last_poll ? ` · last look ${escapeHtml(s.last_poll)}` : ""}${s.next_poll ? ` · next ~${escapeHtml(s.next_poll)}` : ""}` +
    `${waiting.length ? `<br>Waiting: ${waiting.join("; ")}` : ""}${queued.length ? `<br>Queued: ${queued.join("; ")}` : ""}` +
    `${(s.events || []).length ? `<br><span class="note">${s.events.slice(-4).map(escapeHtml).join("<br>")}</span>` : ""}`;
  const prev = s.preview || [];
  if ($("aqPreviewList")) {
    $("aqPreviewList").innerHTML = prev.length
      ? `<strong>Would be picked up next</strong> (${prev.length}):<br>` + prev.map(f =>
          `${escapeHtml(f.folder)} · ${f.images} images · ${escapeHtml(f.why)}${f.waiting ? ` · ${escapeHtml(f.waiting)}` : ""}`
        ).join("<br>")
      : `<span class="note">Nothing newer than the cutoff would be picked up right now.</span>`;
  }
}
if ($("aqCutoffNow")) $("aqCutoffNow").onclick = () => {
  const d = new Date();
  const pad = n => String(n).padStart(2, "0");
  // datetime-local is timezone-naive; the M5 / this browser should already be Europe/London.
  $("aqCutoff").value = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
if ($("aqSave")) $("aqSave").onclick = async () => {
  try {
    await api("/api/autoqueue/set", { method: "POST", body: JSON.stringify({
      enabled: $("aqEnabled").checked, profile_id: $("aqProfile").value,
      preview_before_apply: $("aqPreview").checked, cutoff: $("aqCutoff") && $("aqCutoff").value }) });
    await loadAutoqueue();
  } catch (e) { alert(e.message); }
};
if ($("aqPoll")) $("aqPoll").onclick = async () => {
  try { await api("/api/autoqueue/poll_now", { method: "POST", body: "{}" }); setTimeout(loadAutoqueue, 4000); }
  catch (e) { alert(e.message); }
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

// ---------------------------------------------------------------- Keyword preview (approve before apply)
const pv = { data: null, shoot: "", id: "", fromJob: false, exKw: new Set(), exFiles: new Set(), timer: null };

async function previewNow() {
  if (!selectedFolder) return alert("Pick a folder");
  const passes = passState.filter(p => p.checked).map(p => p.id);
  if (!passes.length) return alert("Tick at least one pass");
  try {
    const r = await api("/api/preview/build", { method: "POST", body: JSON.stringify({
      folder: selectedFolder, passes, profile: $("profile").value,
      afpoints: $("afPoints").checked, apply_rank_keywords: $("rankKw").checked }) });
    openPreview(r.shoot, r.id, false);
  } catch (e) { alert("Preview failed: " + e.message); }
}

async function openPreview(shoot, id, fromJob) {
  Object.assign(pv, { shoot, id, fromJob, exKw: new Set(), exFiles: new Set() });
  try { pv.data = await api(`/api/preview?shoot=${encodeURIComponent(shoot)}&id=${encodeURIComponent(id)}`); }
  catch (e) { return alert("Could not load preview: " + e.message); }
  $("pvPass").innerHTML = `<option value="">All passes</option>` +
    (pv.data.sources || []).map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
  $("pvFilter").value = ""; $("pvCheckMsg").textContent = "";
  $("pvDiscard").classList.toggle("hidden", !fromJob);
  $("pvModal").classList.remove("hidden");
  renderPreview();
  if (pv.data.check && pv.data.check.status === "running") pollCheck();
}

function closePreview() { $("pvModal").classList.add("hidden"); clearTimeout(pv.timer); }

function pvSelectedCount() {
  let kw = 0; const frames = new Set();
  for (const fr of pv.data.items || []) {
    if (pv.exFiles.has(fr.filename)) continue;
    for (const x of fr.add) {
      if (pv.exKw.has(x.keyword) || ($("pvPass").value && x.pass !== $("pvPass").value)) continue;
      kw++; frames.add(fr.filename);
    }
  }
  return { kw, frames: frames.size };
}

function renderPreview() {
  const d = pv.data; if (!d) return;
  $("pvTitle").textContent = `Keyword preview · ${d.shoot}`;
  const rev = Object.entries(d.needs_review_not_applied || {}).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join(", ");
  $("pvSummary").innerHTML = `${d.keywords_new} new keywords on ${d.frames_with_new} frames (${d.keywords_already} already there). ` +
    `Passes: ${(d.sources || []).join(", ")}${(d.missing_sources || []).length ? ` · no results yet for ${d.missing_sources.join(", ")}` : ""}.` +
    `${rev ? ` Not applied, sent to Review: ${escapeHtml(rev)}.` : ""} <br>Compared with: ${escapeHtml(d.compared_with || "")}. ` +
    `Keywords are only ever added; ratings and colour tags are never changed.`;
  const passF = $("pvPass").value;
  const kwCounts = {};
  for (const fr of d.items || []) for (const x of fr.add) if (!passF || x.pass === passF) kwCounts[x.keyword] = (kwCounts[x.keyword] || 0) + 1;
  $("pvKeywords").innerHTML = Object.entries(kwCounts).sort((a, b) => b[1] - a[1]).map(([k, n]) =>
    `<label class="inline pv-chip"><input type="checkbox" data-kw="${escapeHtml(k)}" ${pv.exKw.has(k) ? "" : "checked"} /> ${escapeHtml(k)} <span class="note">${n}</span></label>`
  ).join("") || `<span class="note">Nothing new to add${passF ? " for this pass" : ""}.</span>`;
  $("pvKeywords").querySelectorAll("[data-kw]").forEach(c => {
    c.onchange = () => { c.checked ? pv.exKw.delete(c.dataset.kw) : pv.exKw.add(c.dataset.kw); renderPreview(); };
  });
  const q = $("pvFilter").value.trim().toLowerCase(), onlyNew = $("pvOnlyNew").checked;
  const rows = (d.items || []).filter(fr => {
    const add = fr.add.filter(x => !passF || x.pass === passF);
    if (onlyNew && !add.length) return false;
    if (!q) return true;
    return fr.filename.toLowerCase().includes(q) || [...fr.add, ...fr.already].some(x => x.keyword.toLowerCase().includes(q));
  });
  const shown = rows.slice(0, 400);
  $("pvRows").innerHTML = shown.map(fr => {
    const add = fr.add.filter(x => !passF || x.pass === passF).map(x =>
      `<span class="pv-tag ${pv.exKw.has(x.keyword) ? "off" : "new"}" title="${escapeHtml(x.pass)}">+ ${escapeHtml(x.keyword)}</span>`).join(" ");
    const al = fr.already.map(x => `<span class="pv-tag old">${escapeHtml(x.keyword)}</span>`).join(" ");
    return `<tr><td><input type="checkbox" data-file="${escapeHtml(fr.filename)}" ${pv.exFiles.has(fr.filename) ? "" : "checked"} /></td>
      <td>${escapeHtml(fr.filename)}</td><td>${add || '<span class="note">none</span>'}</td><td>${al}</td></tr>`;
  }).join("");
  $("pvRows").querySelectorAll("[data-file]").forEach(c => {
    c.onchange = () => { c.checked ? pv.exFiles.delete(c.dataset.file) : pv.exFiles.add(c.dataset.file); updatePvCount(rows.length, shown.length); };
  });
  updatePvCount(rows.length, shown.length);
}

function updatePvCount(nRows, nShown) {
  const s = pvSelectedCount();
  $("pvCount").textContent = `${nRows} frames match${nShown < nRows ? ` (first ${nShown} shown)` : ""} · selected to apply: ${s.kw} keywords on ${s.frames} frames`;
  $("pvApprove").disabled = !s.kw;
  $("pvApprove").textContent = s.kw ? `Approve & apply ${s.kw} keywords` : "Nothing selected";
}

async function pvRunCheck() {
  try {
    await api("/api/preview/check", { method: "POST", body: JSON.stringify({ shoot: pv.shoot, id: pv.id }) });
    $("pvCheckMsg").textContent = "Checking against Capture One (read only)…";
    pollCheck();
  } catch (e) { $("pvCheckMsg").textContent = e.message; }
}

function pollCheck() {
  clearTimeout(pv.timer);
  pv.timer = setTimeout(async () => {
    try {
      const c = await api(`/api/preview/check_status?id=${encodeURIComponent(pv.id)}`);
      if (c.status === "running") { $("pvCheckMsg").textContent = `Checking against Capture One… ${c.done_chunks || 0} chunks done`; return pollCheck(); }
      if (c.status === "error") { $("pvCheckMsg").textContent = "Check failed: " + c.error; return; }
      if (c.status === "done") {
        pv.data = await api(`/api/preview?shoot=${encodeURIComponent(pv.shoot)}&id=${encodeURIComponent(pv.id)}`);
        $("pvCheckMsg").textContent = `Checked against Capture One at ${c.finished}.`; renderPreview();
      }
    } catch (e) { $("pvCheckMsg").textContent = e.message; }
  }, 3000);
}

async function pvApprove() {
  const passF = $("pvPass").value;
  const s = pvSelectedCount();
  if (!s.kw) return;
  if (!confirm(`Add ${s.kw} keywords to ${s.frames} frames in Capture One?\n\nKeywords are only added (nothing removed); ratings and colour tags are not touched. You can undo the job from History.`)) return;
  const exclude_passes = passF ? (pv.data.sources || []).filter(x => x !== passF) : [];
  try {
    const r = await api("/api/preview/approve", { method: "POST", body: JSON.stringify({
      shoot: pv.shoot, id: pv.id, confirm: true, exclude_keywords: [...pv.exKw], exclude_passes, exclude_files: [...pv.exFiles] }) });
    closePreview();
    alert(`Queued: ${r.approved.keywords} keywords on ${r.approved.frames} frames (job ${r.job.id}).`);
    refresh();
  } catch (e) { alert("Approve failed: " + e.message); }
}

async function pvDiscard() {
  if (!confirm("Discard this preview? Nothing has been written; the job is closed without applying.")) return;
  try { await api("/api/preview/discard", { method: "POST", body: JSON.stringify({ id: pv.id }) }); closePreview(); refresh(); }
  catch (e) { alert(e.message); }
}

function wirePreview() {
  if (!$("pvModal")) return;
  if ($("previewNow")) $("previewNow").onclick = previewNow;
  $("pvClose").onclick = closePreview;
  $("pvFilter").oninput = renderPreview;
  $("pvPass").onchange = renderPreview;
  $("pvOnlyNew").onchange = renderPreview;
  $("pvCheck").onclick = pvRunCheck;
  $("pvApprove").onclick = pvApprove;
  $("pvDiscard").onclick = pvDiscard;
}
wirePreview();



/* ---- training ---- */
function fmtAuc(x) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  return Number(x).toFixed(3);
}
function aucClass(before, after) {
  if (before == null || after == null) return "";
  if (after > before + 1e-9) return "up";
  if (after < before - 1e-9) return "down";
  return "";
}
function setupTrainingTab() {
  if (!$("trainStart")) return;
  $("trainStart").onclick = startTraining;
  $("trainAdopt").onclick = adoptTraining;
  $("trainRollback").onclick = rollbackTraining;
}
async function startTraining() {
  $("trainStartNote").textContent = "Queuing…";
  try {
    const r = await api("/api/train/start", { method: "POST", body: JSON.stringify({
      index_missing: $("trainIndexMissing").checked,
      auto_adopt: $("trainAutoAdopt").checked,
    })});
    $("trainStartNote").textContent = "Queued " + r.job.id + " — watch Progress below. This never writes to Capture One.";
    await loadTrainingTab();
    refresh();
  } catch (e) {
    $("trainStartNote").textContent = e.message || "Could not queue training";
  }
}
async function adoptTraining() {
  if (!confirm("Adopt the candidate ranking weights from the last report?\n\nThis only updates config.json. Capture One, stars, colour tags and rank keywords are not changed. Current weights are saved so you can Roll back.")) return;
  try {
    const r = await api("/api/train/adopt", { method: "POST", body: JSON.stringify({ confirm: true })});
    $("trainStartNote").textContent = r.reason || "Adopted";
    await loadTrainingTab();
  } catch (e) {
    alert(e.message || "Adopt failed");
  }
}
async function rollbackTraining() {
  if (!confirm("Roll back to the previous ranking weights?\n\nThis only updates config.json. Capture One, stars, colour tags and rank keywords are not changed. The weights you are leaving are saved so you can Adopt them again.")) return;
  try {
    const r = await api("/api/train/rollback", { method: "POST", body: JSON.stringify({ confirm: true })});
    $("trainStartNote").textContent = r.reason || "Rolled back";
    await loadTrainingTab();
  } catch (e) {
    alert(e.message || "Roll back failed");
  }
}
function renderTrainJob(s) {
  const box = $("trainJob");
  const j = s.active_job;
  if (!j) {
    const last = (s.recent_jobs || [])[0];
    if (!last) {
      box.innerHTML = `<p class="note">No training job queued yet.</p>`;
      return;
    }
    const log = (last.log_tail || []).slice(-16).join("\n");
    box.innerHTML = `<div class="job">
      <div class="title">Last run <span class="badge ${last.status}">${escapeHtml(last.status)}</span></div>
      <div class="meta">${escapeHtml(last.id || "")} · finished ${escapeHtml(last.finished || "")}</div>
      ${last.error ? `<div class="meta" style="color:var(--danger)">${escapeHtml(last.error)}</div>` : ""}
      <div class="log">${escapeHtml(log)}</div></div>`;
    return;
  }
  const pct = j.progress && j.progress.total ? Math.round(100 * j.progress.n / j.progress.total) : (j.status === "done" ? 100 : 0);
  const log = (j.log_tail || []).slice(-20).join("\n");
  box.innerHTML = `<div class="job">
    <div class="top"><div>
      <div class="title">Rank training <span class="badge ${j.status}">${escapeHtml(j.status)}</span></div>
      <div class="meta">${fmtProgress(j)}</div>
    </div>
    ${j.status === "queued" || j.status === "running" ? `<button class="danger" id="trainCancel">Cancel</button>` : ""}
    </div>
    <div class="bar"><span style="width:${pct}%"></span></div>
    <div class="log">${escapeHtml(log)}</div></div>`;
  const btn = $("trainCancel");
  if (btn) btn.onclick = async () => {
    await api("/api/queue/cancel", { method: "POST", body: JSON.stringify({ id: j.id })});
    await loadTrainingTab();
    refresh();
  };
}
function renderTrainReport(s) {
  const box = $("trainReport");
  const report = s.report;
  const rank = s.rank || {};
  const state = $("trainRankState");
  const bits = [];
  if (rank.model) bits.push(`model ${rank.model}`);
  if (rank.trained) bits.push(`trained ${rank.trained}`);
  if (rank.adopted) bits.push("adopted");
  if (rank.adopt_reason) bits.push(rank.adopt_reason);
  state.textContent = bits.join(" · ") || "No ranking weights recorded yet.";
  $("trainAdopt").disabled = !rank.has_candidate;
  $("trainRollback").disabled = !rank.has_previous;
  if (!report) {
    box.innerHTML = `<p class="note">No training report yet. Queue a run to generate one.</p>`;
    return;
  }
  const train = report.train || {};
  const sourcesUsed = report.sources_used || [];
  const before = (train.before && train.before.overall) || {};
  const after = train.after_loso_macro || {};
  const labels = report.label_summaries || [];
  const used = labels.filter(r => !r.skipped);
  const skipped = labels.filter(r => r.skipped);
  const totals = used.reduce((a, r) => {
    a.indexed += r.indexed || 0;
    a.combined += r.positives_combined || 0;
    a.exp += r.positives_export_only || 0;
    a.star += r.positives_star_only || 0;
    a.both += r.positives_both || 0;
    a.neg += r.negatives || 0;
    return a;
  }, { indexed: 0, combined: 0, exp: 0, star: 0, both: 0, neg: 0 });
  const metric = (key) => {
    const b = before[key], a = after[key];
    const cls = aucClass(b, a);
    return `<span class="${cls}">${fmtAuc(b)} → ${fmtAuc(a)}</span>`;
  };
  const loso = train.loso || [];
  const rows = loso.map(row => {
    const b = row.before || {}, a = row.after || {};
    return `<tr>
      <td>${escapeHtml(row.held_out)}</td>
      <td class="${aucClass(b.auc_combined, a.auc_combined)}">${fmtAuc(b.auc_combined)} → ${fmtAuc(a.auc_combined)}</td>
      <td class="${aucClass(b.auc_export, a.auc_export)}">${fmtAuc(b.auc_export)} → ${fmtAuc(a.auc_export)}</td>
      <td class="${aucClass(b.auc_star, a.auc_star)}">${fmtAuc(b.auc_star)} → ${fmtAuc(a.auc_star)}</td>
    </tr>`;
  }).join("");
  const srcLines = sourcesUsed.map(s =>
    `<div class="train-source">
      <span class="badge-train">Training data</span>
      <strong>${escapeHtml(s.label || s.key || "Folder")}</strong>
      <span class="note">${escapeHtml(s.role || "")}</span>
      <span class="note">${escapeHtml(s.path || "")}</span>
    </div>`
  ).join("") || `<p class="note">No source folders were recorded in this report.</p>`;
  box.innerHTML = `
    <p class="note">Report ${escapeHtml(report.stamp || "")}${train.adopt_reason ? " · " + escapeHtml(train.adopt_reason) : ""}. Training is read-only on the NAS and does not write to Capture One.</p>
    <h3 style="margin:12px 0 4px;font-size:13px">Folders used</h3>
    ${srcLines}
    <table class="train-table">
      <thead><tr><th>Macro AUC</th><th>Before</th><th>After (held-out)</th></tr></thead>
      <tbody>
        <tr><td>Combined</td><td>${fmtAuc(before.auc_combined_macro)}</td><td>${metric("auc_combined_macro")}</td></tr>
        <tr><td>Export</td><td>${fmtAuc(before.auc_export_macro)}</td><td>${metric("auc_export_macro")}</td></tr>
        <tr><td>Star</td><td>${fmtAuc(before.auc_star_macro)}</td><td>${metric("auc_star_macro")}</td></tr>
      </tbody>
    </table>
    <p class="note" style="margin-top:10px">${used.length} shoots used
      (${totals.indexed} indexed frames, ${totals.combined} combined positives
      — ${totals.exp} export only, ${totals.star} star only, ${totals.both} both; ${totals.neg} negatives).
      ${skipped.length ? skipped.length + " shoots skipped (index too small)." : ""}</p>
    <table class="train-table">
      <thead><tr><th>Held-out shoot</th><th>Combined</th><th>Export</th><th>Star</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="4">No per-shoot rows.</td></tr>`}</tbody>
    </table>
    <table class="train-table">
      <thead><tr><th>Shoot</th><th>Indexed</th><th>Export+</th><th>Star+</th><th>Both</th><th>Combined+</th><th>Negatives</th></tr></thead>
      <tbody>${used.map(r => `<tr>
        <td>${escapeHtml(r.folder)}</td>
        <td>${r.indexed ?? "—"}</td>
        <td>${r.positives_export_only ?? "—"}</td>
        <td>${r.positives_star_only ?? "—"}</td>
        <td>${r.positives_both ?? "—"}</td>
        <td>${r.positives_combined ?? "—"}</td>
        <td>${r.negatives ?? "—"}</td>
      </tr>`).join("")}</tbody>
    </table>`;
}
async function loadTrainingTab() {
  if (!$("trainRoots")) return;
  const s = await api("/api/train/status");
  const box = $("trainRoots");
  const items = [];
  (s.roots || []).forEach(r => items.push(r));
  if (s.imports) items.push(s.imports);
  const marked = items.filter(r => r.marked);
  box.innerHTML = marked.map(r => {
    return `<div class="train-source ${r.available ? "" : "off"}">
      <span class="badge-train">Training data</span>
      <strong>${escapeHtml(r.label)}</strong>
      <span class="note">${escapeHtml(r.role || "")}${r.available ? "" : " — not mounted"}</span>
      <span class="note">${escapeHtml(r.path || "")}</span>
    </div>`;
  }).join("") || `<p class="note">No folders are marked as training data. Turn the Training data toggle on in Settings → Locations.</p>`;
  const markedExports = (s.roots || []).filter(r => r.marked);
  const avail = markedExports.filter(r => r.available).length;
  const nMark = marked.length;
  $("trainRootsNote").textContent = nMark
    ? `${nMark} marked source${nMark === 1 ? "" : "s"} (${avail} export folder${avail === 1 ? "" : "s"} mounted). Change markers in Settings → Locations.`
    : "No sources are marked.";
  const busy = !!(s.active_job && (s.active_job.status === "queued" || s.active_job.status === "running"));
  $("trainStart").disabled = busy || !avail;
  renderTrainJob(s);
  renderTrainReport(s);
}


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
