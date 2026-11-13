/* Schedules tab: spreadsheet editor, history, pre-loaded events. Relies on api() and $() from app.js. */
(function () {
  const HINTS = {
    gig: { act: "Act name", start: "Set start", end: "Set end", doors: "Doors / 開場", notes: "Notes" },
    wedding: { act: "Moment or group", start: "Start", end: "End", doors: "Arrival", notes: "Notes" },
    corporate: { act: "Segment", start: "Start", end: "End", doors: "Doors", notes: "Notes" }
  };

  let state = {
    events: [],
    meta: blankMeta(),
    rows: [blankRow()],
    mtime: null,
    validation: null,
    pastePreview: null,
    dirty: false
  };

  function blankMeta() {
    const today = new Date();
    const date = today.toISOString().slice(0, 10);
    return {
      date: "",
      event: "",
      venue: "",
      timezone: "",
      changeover_buffer_min: 3,
      event_type: "gig",
      source: "manual"
    };
  }

  function blankRow() {
    return { act: "", start: "", end: "", doors: "", notes: "" };
  }

  function hints() {
    return HINTS[state.meta.event_type] || HINTS.gig;
  }

  function readFormMeta() {
    state.meta = {
      date: $("seDate").value,
      event: $("seEvent").value.trim(),
      venue: $("seVenue").value.trim(),
      timezone: $("seTz").value,
      changeover_buffer_min: Number($("seBuf").value || 3),
      event_type: $("seType").value,
      source: "manual"
    };
  }

  function readRows() {
    const rows = [];
    document.querySelectorAll("#seTable .se-row").forEach(tr => {
      rows.push({
        act: tr.querySelector('[data-col="act"]').value,
        start: tr.querySelector('[data-col="start"]').value,
        end: tr.querySelector('[data-col="end"]').value,
        doors: tr.querySelector('[data-col="doors"]').value,
        notes: tr.querySelector('[data-col="notes"]').value
      });
    });
    state.rows = rows.length ? rows : [blankRow()];
    return state.rows;
  }

  function folderPreview() {
    const d = $("seDate").value;
    const n = ($("seEvent").value || "Untitled event").trim();
    const year = d ? d.slice(0, 4) : "YYYY";
    const day = d || "YYYY-MM-DD";
    $("seFolderPath").textContent = `Schedules/${year}/${day}/${n}/schedule.json`;
  }

  function japanNote() {
    const d = $("seDate").value;
    const note = $("seTzNote");
    if (!note) return;
    if (d >= "2026-11-19" && d <= "2026-11-28") {
      note.textContent = "Japan trip dates use Japan time. The camera clock should be on Japan time too (the 19 Nov reminder).";
    } else {
      note.textContent = "";
    }
  }

  function setMetaToForm() {
    const m = state.meta;
    $("seDate").value = m.date || "";
    $("seEvent").value = m.event || "";
    $("seVenue").value = m.venue || "";
    $("seTz").value = m.timezone || "Europe/London";
    $("seBuf").value = m.changeover_buffer_min ?? 3;
    $("seType").value = m.event_type || "gig";
    applyHints();
    folderPreview();
    japanNote();
  }

  function applyHints() {
    const h = hints();
    document.querySelectorAll("#seTable [data-hint]").forEach(el => {
      const col = el.getAttribute("data-hint");
      if (h[col]) el.textContent = h[col];
    });
    document.querySelectorAll("#seTable [data-col]").forEach(inp => {
      const col = inp.getAttribute("data-col");
      if (h[col] && inp.tagName === "INPUT") inp.placeholder = h[col];
    });
  }

  function issuesFor(i) {
    const val = state.validation || {};
    const items = [...(val.errors || []), ...(val.warnings || [])].filter(x => x.row === i);
    return items;
  }

  function renderTable() {
    const body = $("seTableBody");
    if (!body) return;
    const rows = state.rows.length ? state.rows : [blankRow()];
    state.rows = rows;
    body.innerHTML = rows.map((r, i) => {
      const issues = issuesFor(i);
      const cls = issues.some(x => x.level === "red") ? "se-err" : (issues.length ? "se-warn" : "");
      const title = issues.map(x => x.message).join(" ");
      return `<tr class="se-row ${cls}" data-i="${i}" title="${escapeHtml(title)}">
        <td data-label="${escapeHtml(hints().act)}"><input data-col="act" value="${escapeHtml(r.act || "")}" /></td>
        <td data-label="${escapeHtml(hints().start)}"><input data-col="start" value="${escapeHtml(r.start || "")}" inputmode="numeric" /></td>
        <td data-label="${escapeHtml(hints().end)}"><input data-col="end" value="${escapeHtml(r.end || "")}" inputmode="numeric" /></td>
        <td data-label="${escapeHtml(hints().doors)}"><input data-col="doors" value="${escapeHtml(r.doors || "")}" inputmode="numeric" /></td>
        <td data-label="${escapeHtml(hints().notes)}"><input data-col="notes" value="${escapeHtml(r.notes || "")}" /></td>
        <td class="se-del"><button type="button" class="secondary se-del-btn" data-del="${i}" title="Delete row">Delete</button></td>
      </tr>`;
    }).join("");
    body.querySelectorAll("input").forEach(inp => {
      inp.oninput = () => { state.dirty = true; };
      inp.onkeydown = onCellKey;
    });
    body.querySelectorAll(".se-del-btn").forEach(btn => {
      btn.onclick = () => {
        readRows();
        const i = Number(btn.dataset.del);
        state.rows.splice(i, 1);
        if (!state.rows.length) state.rows = [blankRow()];
        state.dirty = true;
        renderTable();
      };
    });
  }

  function onCellKey(ev) {
    if (ev.key === "Enter") {
      ev.preventDefault();
      readRows();
      const tr = ev.target.closest(".se-row");
      const i = Number(tr.dataset.i);
      state.rows.splice(i + 1, 0, blankRow());
      renderTable();
      const next = $("seTableBody").querySelector(`.se-row[data-i="${i + 1}"] input[data-col="act"]`);
      if (next) next.focus();
    }
  }

  function showIssues() {
    const box = $("seIssues");
    const val = state.validation;
    if (!val) { box.innerHTML = ""; return; }
    const bits = [];
    (val.errors || []).forEach(e => bits.push(`<li class="se-red">${escapeHtml(e.message)}</li>`));
    (val.warnings || []).forEach(e => bits.push(`<li class="se-amber">${escapeHtml(e.message)}</li>`));
    const ack = (val.needs_ack) ? `<label class="inline"><input type="checkbox" id="seAck" /> I have checked the warnings and want to save anyway</label>` : "";
    box.innerHTML = bits.length ? `<ul class="se-issues">${bits.join("")}</ul>${ack}` : `<p class="note">Looks good.</p>`;
  }

  async function validateSilent() {
    readFormMeta();
    readRows();
    if (!state.meta.date && !state.meta.event && state.rows.every(r => !r.act && !r.start)) {
      state.validation = null;
      showIssues();
      return;
    }
    try {
      const r = await api("/api/schedules/validate", { method: "POST", body: JSON.stringify({ ...state.meta, rows: state.rows }) });
      state.validation = r.validation;
      showIssues();
      renderTable();
    } catch (e) {
      $("seIssues").textContent = e.message;
    }
  }

  async function saveDraft() {
    readFormMeta();
    readRows();
    if (!state.meta.date || !state.meta.event) return;
    try {
      await api("/api/schedules/draft", { method: "POST", body: JSON.stringify({ ...state.meta, rows: state.rows }) });
      $("seDraftNote").textContent = "Draft saved on this Mac (not on the NAS).";
    } catch (e) {
      $("seDraftNote").textContent = e.message;
    }
  }

  function newEvent() {
    state.meta = blankMeta();
    state.rows = [blankRow(), blankRow(), blankRow()];
    state.mtime = null;
    state.validation = null;
    state.dirty = false;
    $("seEditorTitle").textContent = "New event";
    $("seDraftNote").textContent = "";
    $("seReview").innerHTML = "";
    setMetaToForm();
    renderTable();
    showIssues();
    $("seEditor").classList.remove("hidden");
  }

  async function openEvent(date, name) {
    const r = await api("/api/schedules/event?date=" + encodeURIComponent(date) + "&name=" + encodeURIComponent(name));
    if (!r.ok && r.error) { alert(r.error); return; }
    const meta = r.meta || blankMeta();
    state.meta = meta;
    state.rows = (r.draft && r.draft.rows && r.draft.rows.length) ? r.draft.rows : (r.rows && r.rows.length ? r.rows : [blankRow()]);
    state.mtime = r.mtime;
    state.dirty = false;
    $("seEditorTitle").textContent = meta.event || name;
    $("seDraftNote").textContent = r.draft ? "A local draft is loaded (not yet saved to the NAS)." : "";
    $("seReview").innerHTML = "";
    setMetaToForm();
    renderTable();
    $("seEditor").classList.remove("hidden");
    await validateSilent();
  }

  async function loadEventList() {
    const box = $("seEventList");
    const r = await api("/api/schedules/events");
    if (!r.ok && r.error) {
      box.innerHTML = `<p class="note se-red">${escapeHtml(r.error)}</p>`;
      updateBadge(0);
      return r;
    }
    state.events = r.events || [];
    if (!state.events.length) {
      box.innerHTML = `<p class="note">${r.exists === false ? "The Schedules folder is not there yet. You can still type an event; Confirm will create its folder." : "No events yet."}</p>`;
    } else {
      box.innerHTML = state.events.map(ev =>
        `<button type="button" class="se-event" data-date="${escapeHtml(ev.date)}" data-name="${escapeHtml(ev.event)}">
          <strong>${escapeHtml(ev.date)}</strong> ${escapeHtml(ev.event)}
          <span class="badge">${ev.acts} act${ev.acts === 1 ? "" : "s"}</span>
          ${ev.has_confirmed ? `<span class="badge">confirmed</span>` : ""}
          ${ev.source ? `<span class="badge">${escapeHtml(ev.source)}</span>` : ""}
        </button>`
      ).join("");
      box.querySelectorAll(".se-event").forEach(btn => {
        btn.onclick = () => openEvent(btn.dataset.date, btn.dataset.name);
      });
    }
    updateBadge(r.waiting_count || 0);
    return r;
  }

  function updateBadge(n) {
    const b = $("schedulesBadge");
    if (!b) return;
    if (n > 0) {
      b.textContent = String(n);
      b.classList.remove("hidden");
    } else {
      b.textContent = "0";
      b.classList.add("hidden");
    }
  }

  async function loadPending() {
    const box = $("sePending");
    const r = await api("/api/schedules/pending");
    if (!r.ok && r.error) {
      box.innerHTML = `<p class="note se-red">${escapeHtml(r.error)}</p>`;
      updateBadge(0);
      return;
    }
    const entries = r.entries || [];
    updateBadge(r.waiting_count || 0);
    if (!entries.length) {
      box.innerHTML = `<p class="note">No pre-loaded schedules yet. Use Load trip to add a batch, or save an event and add it later.</p>`;
      return;
    }
    const rows = entries.map(e => {
      const st = e.state || "";
      return `<tr>
        <td>${escapeHtml(e.date || "")}</td>
        <td>${escapeHtml(e.event || "")}<div class="note">${escapeHtml(e.venue || "")}</div></td>
        <td>${escapeHtml((e.activation || "").replace("T", " "))}</td>
        <td><span class="badge ${escapeHtml(st)}">${escapeHtml(st)}</span></td>
        <td class="se-actions">
          <button type="button" class="secondary" data-edit="${escapeHtml(e.id)}">Edit</button>
          <button type="button" class="secondary" data-hold="${escapeHtml(e.id)}">Hold</button>
          <button type="button" class="secondary" data-act="${escapeHtml(e.id)}">Activate now</button>
          <button type="button" class="secondary" data-rm="${escapeHtml(e.id)}">Remove</button>
        </td>
      </tr>`;
    }).join("");
    box.innerHTML = `<table class="data"><thead><tr><th>Date</th><th>Event</th><th>Activation</th><th>State</th><th></th></tr></thead><tbody>${rows}</tbody></table>
      <p class="note">Remove takes the event off this list only. It never deletes the schedule file.</p>`;
    box.querySelectorAll("[data-edit]").forEach(b => b.onclick = () => {
      const e = entries.find(x => x.id === b.dataset.edit);
      if (e) openEvent(e.date, e.event);
    });
    box.querySelectorAll("[data-hold]").forEach(b => b.onclick = async () => {
      await api("/api/schedules/manifest/hold", { method: "POST", body: JSON.stringify({ id: b.dataset.hold }) });
      await loadPending();
    });
    box.querySelectorAll("[data-act]").forEach(b => b.onclick = async () => {
      await api("/api/schedules/manifest/activate", { method: "POST", body: JSON.stringify({ id: b.dataset.act }) });
      await loadPending();
    });
    box.querySelectorAll("[data-rm]").forEach(b => b.onclick = async () => {
      if (!confirm("Remove this event from the pre-load list? The schedule file is kept.")) return;
      await api("/api/schedules/manifest/remove", { method: "POST", body: JSON.stringify({ id: b.dataset.rm }) });
      await loadPending();
    });
  }

  async function reviewAndSave() {
    readFormMeta();
    readRows();
    $("seReview").textContent = "Checking…";
    const review = await api("/api/schedules/review", { method: "POST", body: JSON.stringify({ ...state.meta, rows: state.rows }) });
    state.validation = review.validation;
    showIssues();
    renderTable();
    if (!review.mounted) {
      $("seReview").innerHTML = `<p class="se-red">${escapeHtml(review.error || "NAS is not mounted.")} Your draft is still saved on this Mac.</p>`;
      await saveDraft();
      return;
    }
    if (review.validation && !review.validation.can_save) {
      $("seReview").innerHTML = `<p class="se-red">Fix the red errors before saving. Nothing was written to the NAS.</p>`;
      return;
    }
    const acts = (review.acts || []).map(a => `${a[0]} ${a[1] || "—"}–${a[2] || "—"}`).join("<br>");
    $("seReview").innerHTML = `
      <div class="se-summary">
        <p><strong>${review.replaces ? "Replace" : "Create"}</strong> <code>${escapeHtml(review.rel)}/schedule.json</code></p>
        <p>Time zone ${escapeHtml(review.timezone)} · changeover ${review.buffer_min} min · source: typed by hand</p>
        <p>${review.rows} row(s):<br>${acts}</p>
        ${(review.validation && review.validation.warnings || []).length ? `<p class="se-amber">There are warnings above. Tick the box if you still want to save.</p>` : ""}
        <div class="row">
          <button type="button" id="seConfirmBtn">Confirm and write to the NAS</button>
          <button type="button" class="secondary" id="seCancelReview">Cancel</button>
        </div>
      </div>`;
    $("seCancelReview").onclick = () => { $("seReview").innerHTML = ""; };
    $("seConfirmBtn").onclick = async () => {
      const ack = $("seAck") ? $("seAck").checked : true;
      if (review.validation && review.validation.needs_ack && !ack) {
        alert("Tick the box to confirm the warnings first.");
        return;
      }
      try {
        const r = await api("/api/schedules/confirm", { method: "POST", body: JSON.stringify({
          ...state.meta, rows: state.rows, confirm: true, ack_warnings: ack, mtime: state.mtime
        }) });
        if (!r.ok && r.changed) {
          if (confirm(r.error + " Overwrite the file on the NAS?")) {
            const r2 = await api("/api/schedules/confirm", { method: "POST", body: JSON.stringify({
              ...state.meta, rows: state.rows, confirm: true, ack_warnings: ack, mtime: state.mtime, force: true
            }) });
            if (!r2.ok) { $("seReview").innerHTML = `<p class="se-red">${escapeHtml(r2.error || "Save failed")}</p>`; return; }
            afterSaved(r2);
            return;
          }
          return;
        }
        if (!r.ok) {
          $("seReview").innerHTML = `<p class="se-red">${escapeHtml(r.error || "Save failed")}</p>`;
          state.validation = r.validation || state.validation;
          showIssues();
          return;
        }
        afterSaved(r);
      } catch (e) {
        $("seReview").innerHTML = `<p class="se-red">${escapeHtml(e.message)}</p>`;
        await saveDraft();
      }
    };
  }

  function afterSaved(r) {
    state.mtime = r.mtime;
    state.dirty = false;
    $("seDraftNote").textContent = "";
    $("seReview").innerHTML = `<p class="note">Saved ${escapeHtml(r.path || "")}.</p>`;
    loadEventList();
    loadPending();
  }

  async function showHistory() {
    readFormMeta();
    if (!state.meta.date || !state.meta.event) { alert("Save or open an event first."); return; }
    const box = $("seHistory");
    box.classList.remove("hidden");
    const r = await api("/api/schedules/history?date=" + encodeURIComponent(state.meta.date) + "&name=" + encodeURIComponent(state.meta.event));
    if (!r.ok && r.error) { box.innerHTML = `<p class="se-red">${escapeHtml(r.error)}</p>`; return; }
    const versions = r.versions || [];
    if (!versions.length) { box.innerHTML = `<p class="note">No history yet. It appears after the first Confirm.</p>`; return; }
    box.innerHTML = `<h3>History</h3>
      <ul class="se-hist">${versions.map(v => `<li>
        <strong>v${String(v.version).padStart(3, "0")}</strong>
        ${escapeHtml((v.time || "").replace("T", " "))}
        · ${escapeHtml(v.source || "")}
        · ${escapeHtml(v.summary || "")}
        <button type="button" class="secondary" data-diff="${v.version}">Compare with current</button>
        <button type="button" class="secondary" data-rb="${v.version}">Roll back to this</button>
      </li>`).join("")}</ul>
      <div id="seDiff"></div>`;
    box.querySelectorAll("[data-diff]").forEach(b => b.onclick = async () => {
      const d = await api("/api/schedules/history/diff?date=" + encodeURIComponent(state.meta.date)
        + "&name=" + encodeURIComponent(state.meta.event) + "&a=" + b.dataset.diff);
      const lines = (d.rows || []).map(row => {
        const left = row.left ? `${row.left.act} ${row.left.start}–${row.left.end}` : "—";
        const right = row.right ? `${row.right.act} ${row.right.start}–${row.right.end}` : "—";
        return `<tr class="se-${row.status}"><td>${escapeHtml(row.status)}</td><td>${escapeHtml(left)}</td><td>${escapeHtml(right)}</td></tr>`;
      }).join("");
      $("seDiff").innerHTML = `<table class="data"><thead><tr><th></th><th>v${b.dataset.diff}</th><th>Current</th></tr></thead><tbody>${lines}</tbody></table>`;
    });
    box.querySelectorAll("[data-rb]").forEach(b => b.onclick = async () => {
      if (!confirm("Roll back to this version? It will be saved as a new version. Nothing in history is deleted. Keywords already applied are left as they are.")) return;
      const r2 = await api("/api/schedules/rollback", { method: "POST", body: JSON.stringify({
        date: state.meta.date, event: state.meta.event, version: Number(b.dataset.rb), confirm: true
      }) });
      if (!r2.ok) { alert(r2.error || "Rollback failed"); return; }
      if (r2.warning) alert(r2.warning);
      await openEvent(state.meta.date, state.meta.event);
      await showHistory();
    });
  }

  async function previewPaste() {
    const text = $("sePaste").value;
    if (!text.trim()) { alert("Paste rows from a spreadsheet first."); return; }
    const r = await api("/api/schedules/preview-paste", { method: "POST", body: JSON.stringify({ text }) });
    const rows = (r.preview && r.preview.rows) || [];
    $("sePastePreview").innerHTML = `
      <p class="note">${r.preview.has_header ? "Header row detected." : "No header row; columns are treated as act, start, end, doors, notes."} ${rows.length} row(s).</p>
      <table class="data"><thead><tr><th>Act</th><th>Start</th><th>End</th><th>Doors</th><th>Notes</th></tr></thead>
      <tbody>${rows.map(row => `<tr><td>${escapeHtml(row.act)}</td><td>${escapeHtml(row.start)}</td><td>${escapeHtml(row.end)}</td><td>${escapeHtml(row.doors)}</td><td>${escapeHtml(row.notes)}</td></tr>`).join("")}</tbody></table>
      <div class="row">
        <button type="button" id="sePasteApply">Use these rows</button>
        <button type="button" class="secondary" id="sePasteCancel">Cancel</button>
      </div>`;
    $("sePasteApply").onclick = () => {
      state.rows = rows.map(row => ({ act: row.act, start: row.start, end: row.end, doors: row.doors, notes: row.notes }));
      if (!state.rows.length) state.rows = [blankRow()];
      state.dirty = true;
      renderTable();
      $("sePastePreview").innerHTML = "";
      $("sePaste").value = "";
      validateSilent();
    };
    $("sePasteCancel").onclick = () => { $("sePastePreview").innerHTML = ""; };
  }

  async function loadTripPreview() {
    const text = $("seTripPaste").value;
    if (!text.trim()) { alert("Paste a table with date and event columns first."); return; }
    const r = await api("/api/schedules/manifest/load-trip", { method: "POST", body: JSON.stringify({ text }) });
    const entries = r.entries || [];
    $("seTripPreview").innerHTML = `
      <p class="note">${entries.length} event(s) will be added as pending. Nothing is written until you confirm.</p>
      <table class="data"><thead><tr><th>Date</th><th>Event</th><th>Activation</th></tr></thead>
      <tbody>${entries.map(e => `<tr><td>${escapeHtml(e.date)}</td><td>${escapeHtml(e.event)}</td><td>${escapeHtml(e.activation)}</td></tr>`).join("")}</tbody></table>
      <div class="row">
        <button type="button" id="seTripConfirm">Confirm and add to the list</button>
        <button type="button" class="secondary" id="seTripCancel">Cancel</button>
      </div>`;
    $("seTripConfirm").onclick = async () => {
      const r2 = await api("/api/schedules/manifest/load-trip", { method: "POST", body: JSON.stringify({ text, confirm: true }) });
      if (!r2.ok) { alert(r2.error || "Could not add events"); return; }
      $("seTripPreview").innerHTML = `<p class="note">Added ${r2.added || 0}, updated ${r2.updated || 0}.</p>`;
      $("seTripPaste").value = "";
      await loadPending();
    };
    $("seTripCancel").onclick = () => { $("seTripPreview").innerHTML = ""; };
  }

  async function onDateChange() {
    const d = $("seDate").value;
    folderPreview();
    japanNote();
    if (!d) return;
    try {
      const r = await api("/api/schedules/defaults?date=" + encodeURIComponent(d) + "&event_type=" + encodeURIComponent($("seType").value));
      if (r.timezone && !$("seTz").dataset.userSet) $("seTz").value = r.timezone;
    } catch (e) { /* leave as-is */ }
  }

  window.loadSchedulesTab = async function loadSchedulesTab() {
    if (!$("tab-schedules")) return;
    await loadEventList();
    await loadPending();
  };

  function wire() {
    if (!$("tab-schedules")) return;
    $("seNew").onclick = newEvent;
    $("seAddRow").onclick = () => { readRows(); state.rows.push(blankRow()); renderTable(); const last = $("seTableBody").querySelector(".se-row:last-child input[data-col='act']"); if (last) last.focus(); };
    $("seValidate").onclick = validateSilent;
    $("seReviewSave").onclick = reviewAndSave;
    $("seHistoryBtn").onclick = showHistory;
    $("sePasteBtn").onclick = previewPaste;
    $("seTripBtn").onclick = loadTripPreview;
    $("seDate").onchange = onDateChange;
    $("seEvent").oninput = folderPreview;
    $("seType").onchange = () => { state.meta.event_type = $("seType").value; applyHints(); renderTable(); };
    $("seTz").onchange = () => { $("seTz").dataset.userSet = "1"; };
    ["seDate", "seEvent", "seVenue", "seTz", "seBuf"].forEach(id => {
      $(id).addEventListener("change", () => { state.dirty = true; saveDraft(); });
    });
    newEvent();
    $("seEditor").classList.add("hidden");
  }

  document.addEventListener("DOMContentLoaded", wire);
})();
