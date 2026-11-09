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
function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let currentShoot = null;
let libraryPath = null;

async function copyPath(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    // fallback
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); return true; }
    finally { ta.remove(); }
  }
}

async function revealPath(path) {
  return api("/api/people/reveal", { method: "POST", body: JSON.stringify({ path }) });
}

function pathRow(label, path) {
  if (!path) return "";
  const id = "p" + Math.random().toString(36).slice(2, 9);
  return `<div class="row-block">
    <div class="note">${escapeHtml(label)}</div>
    <div class="pe-path-row" data-path="${escapeHtml(path)}">
      <code title="${escapeHtml(path)}">${escapeHtml(path)}</code>
      <button type="button" data-copy>Copy path</button>
      <button type="button" data-reveal>Show in Finder</button>
    </div>
  </div>`;
}

function wirePathRows(root) {
  if (!root) return;
  root.querySelectorAll(".pe-path-row").forEach(row => {
    const path = row.dataset.path;
    row.querySelector("[data-copy]").onclick = async () => {
      const ok = await copyPath(path);
      const btn = row.querySelector("[data-copy]");
      const prev = btn.textContent;
      btn.textContent = ok ? "Copied" : "Copy failed";
      setTimeout(() => { btn.textContent = prev; }, 1200);
    };
    row.querySelector("[data-reveal]").onclick = async () => {
      const btn = row.querySelector("[data-reveal]");
      const prev = btn.textContent;
      btn.disabled = true;
      try {
        await revealPath(path);
        btn.textContent = "Opened";
      } catch (e) {
        alert(e.message || "Could not reveal");
        btn.textContent = prev;
      } finally {
        btn.disabled = false;
        setTimeout(() => { btn.textContent = prev; }, 1200);
      }
    };
  });
}

async function loadLibrary() {
  const lib = await api("/api/people/library");
  libraryPath = lib.library_path || null;
  document.getElementById("libCount").textContent = `Library: ${lib.count} people`;
  const box = document.getElementById("library");
  if (!lib.people.length) {
    box.innerHTML = `<span class="note">Empty — name a cluster below</span>`;
  } else {
    box.innerHTML = lib.people.map(p =>
      `<span title="${escapeHtml((p.shoots||[]).join(', '))}">${escapeHtml(p.name)} · ${p.face_count||0}</span>`
    ).join("");
  }
  const lp = document.getElementById("libPaths");
  lp.innerHTML = `<h3>On this Mac</h3>
    ${pathRow("People library (names + embeddings)", lib.library_path)}
    ${pathRow("People folder", lib.people_dir)}`;
  wirePathRows(lp);
}

async function loadShoots() {
  const data = await api("/api/people/shoots");
  const box = document.getElementById("shoots");
  box.innerHTML = "";
  if (!data.shoots.length) {
    box.innerHTML = `<p class="note">No People runs yet.</p>`;
    return;
  }
  for (const s of data.shoots) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = `${s.shoot}  (${s.clusters||0} clusters, ${s.faces_embedded||0} faces)`;
    b.onclick = () => {
      [...box.querySelectorAll("button")].forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      loadClusters(s.shoot);
    };
    box.appendChild(b);
  }
  if (data.shoots[0]) {
    box.querySelector("button").classList.add("active");
    loadClusters(data.shoots[0].shoot);
  }
}

async function loadClusters(shoot) {
  currentShoot = shoot;
  const data = await api("/api/people/clusters?shoot=" + encodeURIComponent(shoot));
  const s = data.summary || {};
  const paths = data.paths || {};
  document.getElementById("shootTitle").textContent = shoot;
  document.getElementById("summary").textContent =
    `${s.frames||0} frames · ${s.faces_embedded||0} faces · ${s.clusters||0} clusters ` +
    `(matched ${s.matched||0}, review ${s.review_clusters||0}, anon ${s.anonymous||0}) · ` +
    `${s.sec_per_frame||"?"}s/frame`;

  const sp = document.getElementById("shootPaths");
  sp.innerHTML = `<h3>Contact sheets on disk</h3>
    ${pathRow("Shoot people folder", paths.people_dir)}
    ${pathRow("Proposals JSON", paths.proposals_json)}
    ${pathRow("Clusters JSON", paths.clusters_json)}
    ${pathRow("Library JSON", paths.library_json || libraryPath)}`;
  wirePathRows(sp);

  const box = document.getElementById("clusters");
  box.innerHTML = "";
  for (const c of data.clusters || []) {
    const card = document.createElement("div");
    card.className = "pe-card";
    const sheet = `/api/people/sheet?shoot=${encodeURIComponent(shoot)}&cluster=${c.cluster}`;
    const label = c.name || `Person ${String(c.cluster).padStart(3,"0")}`;
    const sheetPath = c.sheet_path || c.contact_sheet || "";
    card.innerHTML = `
      <img src="${sheet}" alt="cluster ${c.cluster}" loading="lazy" />
      <div><strong>${escapeHtml(label)}</strong>
        <span class="pe-badge ${escapeHtml(c.status||'anonymous')}">${escapeHtml(c.status||'anonymous')}</span>
      </div>
      <div class="meta">${c.size||0} faces${c.similarity!=null ? ` · sim ${c.similarity}` : ""}</div>
      ${sheetPath ? pathRow("Contact sheet file", sheetPath) : `<div class="note">No contact sheet file on disk</div>`}
      <label>Name
        <input type="text" value="${escapeHtml(c.name||"")}" placeholder="e.g. Yuki" data-cluster="${c.cluster}" />
      </label>
      <button type="button" data-save="${c.cluster}">Save name to library</button>
    `;
    wirePathRows(card);
    card.querySelector("[data-save]").onclick = async () => {
      const name = card.querySelector("input").value.trim();
      if (!name) return alert("Enter a name");
      try {
        await api("/api/people/name", { method: "POST", body: JSON.stringify({
          shoot, cluster: c.cluster, name
        })});
        await loadLibrary();
        await loadClusters(shoot);
      } catch (e) { alert(e.message); }
    };
    box.appendChild(card);
  }
  if (!(data.clusters||[]).length) {
    box.innerHTML = `<p class="note">No clusters in this shoot.</p>`;
  }
}

(async () => {
  try {
    const s = await api("/api/login_status");
    if (!s.ok) {
      document.body.innerHTML = `<div class="wrap"><p>Please <a href="/">log in</a> to the control panel first.</p></div>`;
      return;
    }
    await loadLibrary();
    await loadShoots();
  } catch (e) {
    document.body.innerHTML = `<div class="wrap"><p>Error: ${escapeHtml(e.message)}. <a href="/">Back</a></p></div>`;
  }
})();
