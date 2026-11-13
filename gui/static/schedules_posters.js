/* Schedule review: poster reads stay local until Confirm. Relies on api() and $() from app.js. */
(function () {
  let items = [];
  let openKey = null;
  let rows = [];
  let meta = {};
  let item = null;

  function keyOf(it) {
    return (it.date || "") + "\t" + (it.event || "");
  }

  function statusLabel(it) {
    return ({
      pending_review: "Needs review",
      multiple: "Multiple images, pick one",
      held: "Held",
      error: "Could not read",
      accepted: "Accepted",
      rejected: "Rejected"
    })[it.status] || it.status || "";
  }

  function showList(review) {
    const box = $("prList");
    if (!box) return;
    const visible = (review || []).filter(it => it.status !== "accepted" && it.status !== "rejected");
    const hiddenN = (review || []).length - visible.length;
    if (!visible.length) {
      box.innerHTML = `<p class="note">No posters waiting for review.${hiddenN ? " " + hiddenN + " already accepted or rejected." : ""}</p>`;
      return;
    }
    box.innerHTML = visible.map(it => {
      const k = keyOf(it);
      const active = k === openKey ? " active" : "";
      const n = it.image_count || (it.images || []).length || 0;
      return `<div class="pr-card${active}">
        <div class="pr-card-head">
          <button type="button" class="pr-open" data-key="${escapeHtml(k)}">
            <strong>${escapeHtml(it.date || "")}</strong> ${escapeHtml(it.event || "")}
            <span class="badge">${escapeHtml(statusLabel(it))}</span>
            ${n ? `<span class="badge">${n} image${n === 1 ? "" : "s"}</span>` : ""}
          </button>
        </div>
      </div>`;
    }).join("");
    box.querySelectorAll(".pr-open").forEach(btn => {
      btn.onclick = () => openItem(btn.dataset.key);
    });
  }

  function renderComparison(cmp) {
    if (!cmp) return `<p class="note">No comparison yet.</p>`;
    if (!cmp.has_confirmed) {
      return `<p class="note">No confirmed.json for this event. The poster can become the schedule once you Confirm.</p>`;
    }
    const flags = [];
    (cmp.matches || []).forEach(m => flags.push(`<span class="pr-flag match">Match: ${escapeHtml(m.act)} ${escapeHtml(m.start)}–${escapeHtml(m.end)}</span>`));
    (cmp.gaps || []).forEach(g => flags.push(`<span class="pr-flag gap">Gap filled: ${escapeHtml(g.act)} ${escapeHtml(g.start)}–${escapeHtml(g.end)}</span>`));
    (cmp.disagreements || []).forEach(d => flags.push(`<span class="pr-flag dis">${escapeHtml(d.reason || d.act)}</span>`));
    if (cmp.order_warning) flags.push(`<span class="pr-flag dis">Act order differs from confirmed.json.</span>`);
    const verdict = cmp.can_accept
      ? `<p class="note">This matches confirmed.json or only fills gaps. Confirm will write schedule.json.</p>`
      : `<p class="se-red">confirmed.json is the master. Edit the rows to match or to fill gaps, or Hold this poster. Accept is blocked while there are disagreements.</p>`;
    return `<div class="pr-cmp">${flags.join("") || "<span class='note'>No timed acts to compare.</span>"}</div>${verdict}`;
  }

  function renderOther(list) {
    const items = list || [];
    if (!items.length) return "";
    const label = {
      sale: "Sale",
      price: "Price",
      merch: "Merch",
      parallel: "Parallel / after-show",
      break: "Break",
      info: "Note",
      date: "Date"
    };
    const rows = items.map(o => {
      const when = (o.start || o.end) ? `${escapeHtml(o.start || "")}${o.end ? "–" + escapeHtml(o.end) : ""}` : "";
      const act = o.act ? escapeHtml(o.act) + " " : "";
      return `<li><span class="pr-flag">${escapeHtml(label[o.kind] || o.kind || "")}</span> ${when} ${act}${escapeHtml(o.text || "")}</li>`;
    }).join("");
    return `<details class="pr-other"><summary>Other information on the poster (sale times, prices, merch) — not used as acts (${items.length})</summary><ul>${rows}</ul></details>`;
  }

  function renderTable() {
    if (!rows.length) rows = [{ act: "", start: "", end: "", doors: "", notes: "" }];
    return `<table id="prTable">
      <thead><tr><th>Act name</th><th>Set start</th><th>Set end</th><th>Doors / 開場</th><th>Notes</th><th></th></tr></thead>
      <tbody>${rows.map((r, i) => `<tr class="pr-row" data-i="${i}">
        <td><input data-col="act" value="${escapeHtml(r.act || "")}" /></td>
        <td><input data-col="start" value="${escapeHtml(r.start || "")}" /></td>
        <td><input data-col="end" value="${escapeHtml(r.end || "")}" /></td>
        <td><input data-col="doors" value="${escapeHtml(r.doors || "")}" /></td>
        <td><input data-col="notes" value="${escapeHtml(r.notes || "")}" /></td>
        <td class="se-del"><button type="button" class="secondary" data-del="${i}">Remove</button></td>
      </tr>`).join("")}</tbody>
    </table>
    <div class="row"><button type="button" class="secondary" id="prAddRow">Add row</button>
    <button type="button" class="secondary" id="prCheck">Check times</button></div>
    <div id="prIssues"></div>`;
  }

  function readRows() {
    const out = [];
    document.querySelectorAll("#prTable .pr-row").forEach(tr => {
      const rec = { act: "", start: "", end: "", doors: "", notes: "" };
      tr.querySelectorAll("input[data-col]").forEach(inp => { rec[inp.dataset.col] = inp.value; });
      out.push(rec);
    });
    rows = out;
    return out;
  }

  function showIssues(val) {
    const box = $("prIssues");
    if (!box) return;
    const bits = [];
    (val.errors || []).forEach(e => bits.push(`<li class="se-red">${escapeHtml(e.message)}</li>`));
    (val.warnings || []).forEach(e => bits.push(`<li class="se-amber">${escapeHtml(e.message)}</li>`));
    const ack = val.needs_ack ? `<label class="inline"><input type="checkbox" id="prAck" /> I have checked the warnings and want to save anyway</label>` : "";
    box.innerHTML = bits.length ? `<ul class="se-issues">${bits.join("")}</ul>${ack}` : (ack || "");
  }

  async function refreshPreview() {
    if (!item) return;
    readRows();
    try {
      const r = await api("/api/schedules/posters/preview", {
        method: "POST",
        body: JSON.stringify({ date: item.date, event: item.event, rows, meta })
      });
      item.comparison = r.comparison;
      item.validation = r.validation;
      item.can_accept = r.can_accept;
      const cmp = $("prCmp");
      if (cmp) cmp.innerHTML = renderComparison(r.comparison);
      showIssues(r.validation || {});
      const acc = $("prAccept");
      if (acc) acc.disabled = !r.can_accept;
    } catch (e) {
      const box = $("prIssues");
      if (box) box.innerHTML = `<p class="se-red">${escapeHtml(e.message)}</p>`;
    }
  }

  function renderDetail() {
    const box = $("prDetail");
    if (!box || !item) { if (box) box.classList.add("hidden"); return; }
    box.classList.remove("hidden");
    const imgName = (item.image && item.image.name) || "";
    const thumb = `/api/schedules/posters/thumb?date=${encodeURIComponent(item.date)}&name=${encodeURIComponent(item.event)}${imgName ? "&file=" + encodeURIComponent(imgName) : ""}`;
    const pick = (item.status === "multiple")
      ? `<p class="note">This folder has more than one image. Pick one to read. Posters are not moved or renamed.</p>
         <div class="row">${(item.images || []).map(n =>
           `<button type="button" class="secondary" data-pick="${escapeHtml(n)}">${escapeHtml(n)}</button>`
         ).join("")}</div>`
      : "";
    const img = (item.status !== "multiple" && (item.image || imgName))
      ? `<img class="pr-thumb" alt="Poster" src="${thumb}" />`
      : "";
    box.innerHTML = `
      <h3>${escapeHtml(item.date || "")} · ${escapeHtml(item.event || "")}</h3>
      <p class="note">${escapeHtml(statusLabel(item))}${item.error ? " — " + escapeHtml(item.error) : ""}. Drafts stay on this Mac until you Confirm.</p>
      ${pick}${img}
      <div id="prCmp">${renderComparison(item.comparison)}</div>
      ${item.status === "multiple" ? "" : renderTable()}
      ${renderOther(item.other)}
      <div class="pr-actions">
        <button type="button" id="prAccept"${item.can_accept ? "" : " disabled"}>Accept and Confirm</button>
        <button type="button" class="secondary" id="prHold">Hold</button>
        <button type="button" class="secondary" id="prReject">Reject</button>
        <button type="button" class="secondary" id="prReread">Re-read</button>
      </div>
      <div id="prSaveMsg"></div>
    `;
    if ($("prAddRow")) $("prAddRow").onclick = () => { readRows(); rows.push({ act: "", start: "", end: "", doors: "", notes: "" }); renderDetail(); };
    if ($("prCheck")) $("prCheck").onclick = refreshPreview;
    box.querySelectorAll("[data-del]").forEach(b => b.onclick = () => {
      readRows(); rows.splice(Number(b.dataset.del), 1); renderDetail();
    });
    box.querySelectorAll("#prTable input").forEach(inp => {
      inp.addEventListener("change", refreshPreview);
    });
    box.querySelectorAll("[data-pick]").forEach(b => b.onclick = () => reread(b.dataset.pick));
    if ($("prAccept")) $("prAccept").onclick = accept;
    if ($("prHold")) $("prHold").onclick = () => setStatus("hold");
    if ($("prReject")) $("prReject").onclick = () => setStatus("reject");
    if ($("prReread")) $("prReread").onclick = () => reread();
    if (item.validation) showIssues(item.validation);
  }

  async function openItem(k) {
    openKey = k;
    const [date, event] = k.split("\t");
    try {
      const r = await api("/api/schedules/posters/item?date=" + encodeURIComponent(date) + "&name=" + encodeURIComponent(event));
      item = r;
      rows = (r.rows || []).map(x => ({ act: x.act || "", start: x.start || "", end: x.end || "", doors: x.doors || "", notes: x.notes || "" }));
      meta = r.meta || { date, event, source: "poster" };
      renderDetail();
      showList(items);
    } catch (e) {
      $("prDetail").classList.remove("hidden");
      $("prDetail").innerHTML = `<p class="se-red">${escapeHtml(e.message)}</p>`;
    }
  }

  async function reread(filename) {
    if (!item) return;
    try {
      const body = { date: item.date, event: item.event };
      if (filename) body.filename = filename;
      const r = await api("/api/schedules/posters/read", { method: "POST", body: JSON.stringify(body) });
      await loadPosterReview(true);
      openKey = keyOf(r);
      await openItem(openKey);
    } catch (e) {
      alert(e.message || "Re-read failed");
    }
  }

  async function setStatus(kind) {
    if (!item) return;
    if (kind === "reject" && !confirm("Reject this poster read? Nothing is written to the Schedules folder. The image is left as it is.")) return;
    try {
      readRows();
      await api("/api/schedules/posters/" + kind, {
        method: "POST",
        body: JSON.stringify({ date: item.date, event: item.event, rows })
      });
      item = null;
      openKey = null;
      $("prDetail").innerHTML = "";
      $("prDetail").classList.add("hidden");
      await loadPosterReview(true);
    } catch (e) {
      alert(e.message || "Could not update");
    }
  }

  async function accept() {
    if (!item) return;
    readRows();
    const ack = $("prAck") ? $("prAck").checked : false;
    const msg = $("prSaveMsg");
    try {
      const preview = await api("/api/schedules/posters/preview", {
        method: "POST",
        body: JSON.stringify({ date: item.date, event: item.event, rows, meta })
      });
      if (!preview.can_accept) {
        item.comparison = preview.comparison;
        item.validation = preview.validation;
        renderDetail();
        if (msg) msg.innerHTML = `<p class="se-red">Accept is blocked until disagreements are edited or the poster is held. Nothing was written.</p>`;
        return;
      }
      if (preview.validation && preview.validation.needs_ack && !ack) {
        showIssues(preview.validation);
        if (msg) msg.innerHTML = `<p class="se-amber">Tick the warning box before Confirm.</p>`;
        return;
      }
      if (!confirm("Write schedule.json to the event folder? This uses the same Confirm path as the spreadsheet editor. The poster image is not moved.")) return;
      const r = await api("/api/schedules/posters/accept", {
        method: "POST",
        body: JSON.stringify({ date: item.date, event: item.event, rows, meta, confirm: true, ack_warnings: ack })
      });
      if (msg) msg.innerHTML = `<p class="note">Saved ${escapeHtml(r.path || "")}.</p>`;
      item = null;
      openKey = null;
      await loadPosterReview(true);
      if (window.loadSchedulesTab) {
        // refresh events list; avoid recursion by calling the inner list if needed
      }
      // reload events quietly
      try { await api("/api/schedules/events"); } catch (e) { /* ignore */ }
      $("prDetail").innerHTML = `<p class="note">Saved ${escapeHtml(r.path || "")}.</p>`;
      $("prDetail").classList.remove("hidden");
    } catch (e) {
      if (msg) msg.innerHTML = `<p class="se-red">${escapeHtml(e.message)}</p>`;
    }
  }

  async function loadPosterReview(scan) {
    const box = $("prList");
    if (!box) return;
    if (scan) box.innerHTML = `<p class="note">Scanning…</p>`;
    try {
      const r = await api("/api/schedules/posters" + (scan ? "?scan=1" : ""));
      items = r.items || r.review || [];
      showList(r.review || items.filter(it => ["pending_review", "multiple", "held", "error"].includes(it.status)));
      const n = r.review_count || 0;
      const b = $("schedulesBadge");
      if (b && n > 0) {
        const cur = Number(b.textContent || 0);
        if (!(b.classList.contains("hidden")) && cur >= n) {
          /* leave combined badge from the events load */ 
        } else if (b.classList.contains("hidden") && n > 0) {
          b.textContent = String(n);
          b.classList.remove("hidden");
        }
      }
    } catch (e) {
      box.innerHTML = `<p class="note se-red">${escapeHtml(e.message)}</p>`;
    }
  }

  window.loadPosterReview = loadPosterReview;

  function wire() {
    if (!$("prScan")) return;
    $("prScan").onclick = () => loadPosterReview(true);
    const prev = window.loadSchedulesTab;
    window.loadSchedulesTab = async function () {
      if (prev) await prev();
      await loadPosterReview(true);
    };
  }

  document.addEventListener("DOMContentLoaded", wire);
})();
