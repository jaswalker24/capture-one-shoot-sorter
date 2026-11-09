let items = [], idx = 0, shoot = null, history = [];
const KIND = new URLSearchParams(location.search).get("kind") === "focus" ? "focus" : "numbers";
const $ = id => document.getElementById(id);
async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opts });
  const d = await r.json().catch(() => ({}));
  if (r.status === 401) { location.href = "/"; throw new Error("login required"); }
  if (!r.ok) throw new Error(d.error || r.statusText);
  return d;
}
function q() { return `shoot=${encodeURIComponent(shoot)}&kind=${KIND}`; }
function setupKind() {
  if (KIND !== "focus") return;
  document.title = "Missed focus review · Trips Local AI";
  document.querySelector("header h1").textContent = "Missed focus review";
  document.querySelector("header .sub").innerHTML = 'The AF focus check thinks the camera\'s AF box (red) is soft while something else is sharp. Confirm adds <b>General/Focus/Missed</b>; reject adds nothing. Only applied when you press Apply. <a href="/">Back to the control panel</a> · <a href="/review">Race numbers</a>';
  $("onlyNums").closest("label").style.display = "none";
  $("fixInput").closest("label").style.display = "none";
  $("bFix").style.display = "none";
  $("applyBtn").textContent = "Apply confirmed Focus/Missed to Capture One";
  $("bConfirm").textContent = "Confirm missed (Enter)";
}
async function init() {
  setupKind();
  const s = await api(`/api/review/shoots?kind=${KIND}`);
  $("shoot").innerHTML = s.shoots.map(x => `<option value="${x.shoot}">${x.shoot} (${x.reviewed || 0}/${x.needs_review || 0})</option>`).join("");
  const want = new URLSearchParams(location.search).get("shoot");
  if (want) $("shoot").value = want;
  shoot = $("shoot").value;
  $("shoot").onchange = () => { shoot = $("shoot").value; load(); };
  $("filter").onchange = load; $("onlyNums").onchange = load;
  $("bConfirm").onclick = () => act("confirm"); $("bReject").onclick = () => act("reject");
  $("bSkip").onclick = () => act("skip"); $("bUndo").onclick = () => act("undo");
  $("bFix").onclick = () => fix(); $("applyBtn").onclick = applyNow;
  await load();
  setInterval(pollApply, 4000);
}
async function load(keepName) {
  const d = await api(`/api/review/queue?${q()}&status=${$("filter").value}&only_numbers=${$("onlyNums").checked ? 1 : 0}`);
  items = d.items; showCounts(d.counts, d.apply);
  idx = 0;
  if (keepName) { const j = items.findIndex(x => x.filename === keepName); if (j >= 0) idx = j; }
  render();
}
function showCounts(c, a) {
  $("counts").innerHTML = `To review <b>${c.pending}</b> · confirmed ${c.confirm} · fixed ${c.fix} · rejected ${c.reject} · skipped ${c.skip} · ready to apply <b>${c.ready_to_apply}</b> · applied ${c.applied}`;
  showApply(a);
}
function showApply(a) {
  if (!a) { $("applyMsg").textContent = ""; return; }
  if (a.status === "running") $("applyMsg").textContent = "Applying… " + ((a.log_tail || []).slice(-1)[0] || "");
  else if (a.status === "done" && a.finished) $("applyMsg").textContent = `Last apply ${a.finished}: ${a.applied_keywords ?? a.applied ?? 0} keywords, ${a.failures || 0} failures` + (a.verify ? `, stars/colours changed: ${a.verify.rating_or_colour_changed}` : "");
  else if (a.status === "error") $("applyMsg").textContent = "Apply failed, see log: " + (a.log || "");
}
function render() {
  const it = items[idx];
  $("strip").innerHTML = items.map((x, i) => `<div title="${x.filename}" class="${x.status} ${i === idx ? "cur" : ""}" data-i="${i}"></div>`).join("");
  $("strip").querySelectorAll("div").forEach(el => el.onclick = () => { idx = +el.dataset.i; render(); });
  if (!it) { $("img").removeAttribute("src"); $("fname").textContent = "Nothing here"; $("proposed").textContent = "–"; $("cands").innerHTML = ""; $("reasons").textContent = ""; $("status").textContent = ""; $("pos").textContent = `0 of 0`; return; }
  $("pos").textContent = `${idx + 1} of ${items.length}`;
  $("img").src = `/api/review/thumb?${q()}&file=${encodeURIComponent(it.filename)}`;
  $("fname").textContent = it.filename;
  $("proposed").textContent = KIND === "focus" ? "Focus missed?" : (it.proposed.length ? it.proposed.join(", ") : "none");
  $("cands").innerHTML = it.candidates.map(c => `<span>${c.number} · ${c.sources.join("+")}${c.conf != null ? " " + (+c.conf).toFixed(2) : ""}</span>`).join("") + (it.car_colour ? `<span>colour: ${it.car_colour}</span>` : "");
  $("reasons").textContent = (it.reasons || []).join(" · ");
  const d = it.decision;
  $("status").textContent = d ? `${d.action}${d.numbers && d.numbers.length ? " → " + d.numbers.join(", ") : ""}${it.applied ? " (in Capture One)" : ""}` : "not reviewed";
  $("fixInput").value = "";
  // prefetch next image
  if (items[idx + 1]) { const im = new Image(); im.src = `/api/review/thumb?${q()}&file=${encodeURIComponent(items[idx + 1].filename)}`; }
}
async function act(action, numbers) {
  const it = items[idx]; if (!it) return;
  try {
    const r = await api("/api/review/decide", { method: "POST", body: JSON.stringify({ shoot, filename: it.filename, action, numbers, kind: KIND }) });
    it.decision = r.decision; it.status = r.decision ? r.decision.action : "pending";
    history.push(it.filename);
    if (action !== "undo") idx = Math.min(idx + 1, items.length - 1);
    render(); refreshCounts();
  } catch (e) { alert(e.message); }
}
function fix() { const v = $("fixInput").value.trim(); if (!v) { $("fixInput").focus(); return; } act("fix", v); $("fixInput").blur(); }
async function refreshCounts() { try { const d = await api(`/api/review/queue?${q()}&status=none`); showCounts(d.counts, d.apply); } catch (e) {} }
async function pollApply() { try { const d = await api(`/api/review/apply_status?${q()}`); showApply(d.apply); } catch (e) {} }
async function applyNow() {
  const msg = KIND === "focus" ? "Add General/Focus/Missed to the confirmed frames in Capture One? Nothing else changes."
                               : "Add the confirmed and fixed car numbers to Capture One as keywords? Nothing else changes.";
  if (!confirm(msg)) return;
  try { const r = await api("/api/review/apply", { method: "POST", body: JSON.stringify({ shoot, kind: KIND }) }); showApply(r.apply); }
  catch (e) { alert(e.message); }
}
document.addEventListener("keydown", e => {
  const inFix = document.activeElement === $("fixInput");
  if (inFix) {
    if (e.key === "Enter") { e.preventDefault(); fix(); }
    else if (e.key === "Escape") { $("fixInput").value = ""; $("fixInput").blur(); }
    return;
  }
  if (e.target.tagName === "SELECT" || e.metaKey || e.ctrlKey) return;
  if (KIND === "numbers" && /^[0-9]$/.test(e.key)) { $("fixInput").focus(); return; }  // digit goes into the box
  const k = e.key.toLowerCase();
  if (e.key === "Enter" || k === "y") { e.preventDefault(); act("confirm"); }
  else if (k === "x" || k === "r") act("reject");
  else if (k === "s") act("skip");
  else if (k === "u") act("undo");
  else if (e.key === "ArrowRight") { idx = Math.min(idx + 1, items.length - 1); render(); }
  else if (e.key === "ArrowLeft") { idx = Math.max(idx - 1, 0); render(); }
});
init();
