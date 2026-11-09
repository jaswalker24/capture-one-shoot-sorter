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

async function loadLibrary() {
  const lib = await api("/api/people/library");
  document.getElementById("libCount").textContent = `Library: ${lib.count} people`;
  const box = document.getElementById("library");
  if (!lib.people.length) {
    box.innerHTML = `<span class="note">Empty — name a cluster below</span>`;
    return;
  }
  box.innerHTML = lib.people.map(p =>
    `<span title="${escapeHtml((p.shoots||[]).join(', '))}">${escapeHtml(p.name)} · ${p.face_count||0}</span>`
  ).join("");
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
  document.getElementById("shootTitle").textContent = shoot;
  document.getElementById("summary").textContent =
    `${s.frames||0} frames · ${s.faces_embedded||0} faces · ${s.clusters||0} clusters ` +
    `(matched ${s.matched||0}, review ${s.review_clusters||0}, anon ${s.anonymous||0}) · ` +
    `${s.sec_per_frame||"?"}s/frame`;
  const box = document.getElementById("clusters");
  box.innerHTML = "";
  for (const c of data.clusters || []) {
    const card = document.createElement("div");
    card.className = "pe-card";
    const sheet = `/api/people/sheet?shoot=${encodeURIComponent(shoot)}&cluster=${c.cluster}`;
    const label = c.name || `Person ${String(c.cluster).padStart(3,"0")}`;
    card.innerHTML = `
      <img src="${sheet}" alt="cluster ${c.cluster}" loading="lazy" />
      <div><strong>${escapeHtml(label)}</strong>
        <span class="pe-badge ${escapeHtml(c.status||'anonymous')}">${escapeHtml(c.status||'anonymous')}</span>
      </div>
      <div class="meta">${c.size||0} faces${c.similarity!=null ? ` · sim ${c.similarity}` : ""}</div>
      <label>Name
        <input type="text" value="${escapeHtml(c.name||"")}" placeholder="e.g. Yuki" data-cluster="${c.cluster}" />
      </label>
      <button type="button" data-save="${c.cluster}">Save name to library</button>
    `;
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
