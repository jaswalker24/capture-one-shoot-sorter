let selectedFolder = null;
let passState = []; // [{id,label,checked,note,...}]
let dragId = null;

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

async function showApp() {
  $("loginView").classList.add("hidden");
  $("appView").classList.remove("hidden");
  const passes = await api("/api/passes");
  const order = passes.default_order;
  const byId = Object.fromEntries(passes.passes.map(p => [p.id, p]));
  passState = order.map(id => ({ ...byId[id], checked: true }));
  // include any not in default (e.g. rank) unchecked at end
  for (const p of passes.passes) {
    if (!passState.find(x => x.id === p.id)) passState.push({ ...p, checked: false });
  }
  renderPasses();
  const years = await api("/api/years");
  const ysel = $("year");
  ysel.innerHTML = years.years.map(y => `<option value="${y}">${y}</option>`).join("");
  if (years.years.length) await loadFolders(years.years[0]);
  ysel.onchange = () => loadFolders(ysel.value);
  const ex = await api("/api/exports");
  $("exportFolder").innerHTML = (ex.folders || []).map(f => `<option value="${f.path}">${f.name}</option>`).join("");
  refresh();
  setInterval(refresh, 2000);
}

async function loadFolders(year) {
  const data = await api("/api/folders?year=" + encodeURIComponent(year));
  const box = $("folders");
  box.innerHTML = "";
  for (const f of data.folders) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = f.images == null ? f.name : `${f.name}  (${f.images} images)`;
    b.onclick = () => {
      selectedFolder = f.path;
      $("folderPath").textContent = f.path;
      [...box.querySelectorAll("button")].forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      // soft-default motorsport context for Brands Hatch
      if (/BrandsHatch/i.test(f.name) && !$("context").value) {
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
    if (!p.available) li.classList.add("disabled");
    li.innerHTML = `
      <span class="handle" title="Drag">☰</span>
      <span class="arrows">
        <button type="button" data-up>▲</button>
        <button type="button" data-down>▼</button>
      </span>
      <span>
        <label class="inline" style="margin:0">
          <input type="checkbox" ${p.checked ? "checked" : ""} ${p.available === false ? "disabled" : ""} />
          <strong>${p.label}</strong>
          <span class="badge">${p.id}</span>
        </label>
        ${p.note ? `<div class="note">${p.note}</div>` : ""}
      </span>
      <span></span>`;
    const cb = li.querySelector("input[type=checkbox]");
    cb.onchange = () => { p.checked = cb.checked; };
    li.querySelector("[data-up]").onclick = () => movePass(idx, -1);
    li.querySelector("[data-down]").onclick = () => movePass(idx, 1);
    li.addEventListener("dragstart", () => { dragId = p.id; li.classList.add("dragging"); });
    li.addEventListener("dragend", () => { dragId = null; li.classList.remove("dragging"); });
    li.addEventListener("dragover", e => { e.preventDefault(); });
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
  const body = {
    folder: selectedFolder,
    profile: $("profile").value,
    context: $("context").value,
    passes,
    apply_each: $("applyEach").checked,
    dry_run: $("dryRun").checked,
    ocr_missing_car_number: $("ocrMissing").checked,
    apply_rank_keywords: $("rankKw").checked,
    sample: Number($("sample").value || 0),
  };
  await api("/api/queue/add", { method: "POST", body: JSON.stringify(body) });
  refresh();
}

function fmtProgress(j) {
  const p = j.progress || {};
  if (!p.total) return j.current_pass ? `Running ${j.current_pass}` : (j.status || "");
  const pct = Math.round(100 * p.n / p.total);
  return `${p.pass || j.current_pass}: ${p.n}/${p.total} (${pct}%)`;
}

function renderQueue(data) {
  const box = $("queue");
  const jobs = (data.jobs || []).filter(j => ["queued", "running", "paused"].includes(j.status) || !j.finished);
  // show active + recent unfinished; also recently finished in list briefly
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
    const results = j.pass_results ? Object.entries(j.pass_results).map(([k, v]) => {
      const a = v.apply ? ` apply=${v.apply.applied_keywords || v.apply.applied || v.apply.skipped || "?"}` : "";
      return `${k}: ${v.elapsed || 0}s${a}`;
    }).join(" · ") : "";
    return `<div class="job" data-id="${j.id}">
      <div class="top">
        <div>
          <div class="title">${j.folder_name || j.folder} <span class="badge ${j.status}">${j.status}</span></div>
          <div class="meta">${(j.passes || []).join(" → ")} · ${j.profile}${j.dry_run ? " · dry-run" : ""}${j.sample ? " · sample " + j.sample : ""}</div>
          <div class="meta">${fmtProgress(j)}${results ? " · " + results : ""}</div>
        </div>
        <div style="display:flex;gap:6px">
          ${j.status === "queued" || j.status === "running" ? `<button class="danger" data-cancel="${j.id}">Cancel</button>` : ""}
        </div>
      </div>
      <div class="bar"><span style="width:${pct}%"></span></div>
      <div class="log">${escapeHtml(log)}</div>
    </div>`;
  }).join("");
  box.querySelectorAll("[data-cancel]").forEach(b => {
    b.onclick = async () => {
      await api("/api/queue/cancel", { method: "POST", body: JSON.stringify({ id: b.dataset.cancel }) });
      refresh();
    };
  });
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function renderHistory() {
  const data = await api("/api/history");
  const box = $("history");
  if (!data.history.length) { box.innerHTML = `<p class="note">No history yet.</p>`; return; }
  box.innerHTML = data.history.slice(0, 15).map(j => {
    const v = j.verify ? ` · ratings/colours changed: ${j.verify.rating_or_colour_changed}` : "";
    return `<div class="job"><div class="title">${j.folder_name || (j.folder || "").split("/").pop()} <span class="badge ${j.status}">${j.status}</span></div>
      <div class="meta">${j.finished || j.created || ""} · ${(j.passes || []).join(", ")}${j.error ? " · " + escapeHtml(j.error) : ""}${v}</div></div>`;
  }).join("");
}

async function renderStats() {
  const s = await api("/api/stats");
  const bits = [];
  if (s.cpu_idle_pct != null) bits.push(`<span class="pill"><strong>${(100 - s.cpu_idle_pct).toFixed(0)}%</strong> CPU</span>`);
  if (s.mem_used_gb != null) bits.push(`<span class="pill"><strong>${s.mem_used_gb}/${s.mem_total_gb} GB</strong> mem</span>`);
  if (s.mem_compressor_gb) bits.push(`<span class="pill warn"><strong>${s.mem_compressor_gb} GB</strong> compressed</span>`);
  bits.push(s.caption_model_loaded
    ? `<span class="pill bad">caption model LOADED (${(s.ollama_models || []).join(", ")})</span>`
    : `<span class="pill ok">caption model off</span>`);
  if (s.fullrun_running) bits.push(`<span class="pill warn">fullrun.py running</span>`);
  if (s.paused) bits.push(`<span class="pill warn">queue paused</span>`);
  $("stats").innerHTML = bits.join("");
}

async function refresh() {
  try {
    const q = await api("/api/queue");
    renderQueue(q);
    await renderHistory();
    await renderStats();
  } catch (e) {
    if (String(e.message).includes("login")) {
      $("appView").classList.add("hidden");
      $("loginView").classList.remove("hidden");
    }
  }
}

$("loginBtn").onclick = doLogin;
$("tokenInput").addEventListener("keydown", e => { if (e.key === "Enter") doLogin(); });
$("addJob").onclick = addJob;
$("pauseBtn").onclick = () => api("/api/queue/pause", { method: "POST", body: "{}" }).then(refresh);
$("resumeBtn").onclick = () => api("/api/queue/resume", { method: "POST", body: "{}" }).then(refresh);
$("runCaptions").onclick = async () => {
  const body = {
    folder: $("exportFolder").value,
    profile: $("exportProfile").value,
    context: $("exportContext").value,
    sample: Number($("exportSample").value || 0),
  };
  try {
    const r = await api("/api/exports/captions", { method: "POST", body: JSON.stringify(body) });
    alert("Captions job started: " + r.job.id);
    refresh();
  } catch (e) { alert(e.message); }
};

async function start() {
  const u = new URL(location.href);
  const urlToken = u.searchParams.get("token");
  if (urlToken) {
    u.searchParams.delete("token");
    history.replaceState({}, "", u.pathname + u.search + u.hash);
    try {
      $("tokenInput").value = urlToken;
      await doLogin();
      return;
    } catch (e) {
      $("loginErr").textContent = (e && e.message) || "Auto-login failed — paste the token from ~/localai/gui/auth.token";
    }
  }
  try { await checkLogin(); } catch (e) {}
}
start();
