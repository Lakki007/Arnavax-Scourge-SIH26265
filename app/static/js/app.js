// Scourge: progressive enhancements. Pages work without JS except live proof progress.
(function () {
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  // dialogs, copy buttons, receipt view toggle
  document.addEventListener("click", function (e) {
    var o = e.target.closest("[data-open]");
    if (o && !o.disabled) {
      var d = document.getElementById(o.getAttribute("data-open"));
      if (d && d.showModal) { d.showModal(); var f = d.querySelector("textarea,input:not([type=hidden]),button"); if (f) f.focus(); }
    }
    var c = e.target.closest("[data-close]");
    if (c) { var dlg = c.closest("dialog"); if (dlg) dlg.close(); }
    var cp = e.target.closest("[data-copy]");
    if (cp) {
      var el = document.getElementById(cp.getAttribute("data-copy"));
      var text = el.firstChild ? el.firstChild.textContent.trim() : el.textContent.trim();
      (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(
        function () { cp.textContent = "Copied"; setTimeout(function () { cp.textContent = "Copy"; }, 1600); },
        function () { cp.textContent = "Select and copy"; });
    }
    var v = e.target.closest("[data-view-set]");
    if (v) {
      var fig = v.closest(".receipt");
      fig.setAttribute("data-view", v.getAttribute("data-view-set"));
      fig.querySelectorAll("[data-view-set]").forEach(function (b) { b.setAttribute("aria-pressed", b === v ? "true" : "false"); });
    }
  });

  // ---- challenge page: what crosses depends on the chosen page ----
  function syncEp() {
    var c = document.querySelector('input[name="endpoint"]:checked'); if (!c) return;
    document.querySelectorAll("[data-ep]").forEach(function (li) { li.hidden = li.getAttribute("data-ep") !== c.value; });
  }
  document.addEventListener("change", function (e) { if (e.target.name === "endpoint") syncEp(); });
  syncEp();

  // ---- trace a compared value to its bytes in the receipt ----
  function trace(row, on) {
    var field = row.getAttribute("data-field"), bad = row.classList.contains("res-row-mismatch");
    document.querySelectorAll('.fv[data-field="' + field + '"]').forEach(function (el) {
      el.classList.toggle("trace", on); el.classList.toggle("bad", on && bad);
      if (on && el.scrollIntoView) { var box = el.closest(".receipt-doc"); if (box && (el.offsetTop < box.scrollTop || el.offsetTop > box.scrollTop + box.clientHeight)) box.scrollTop = el.offsetTop - 40; }
    });
  }
  ["mouseover", "focusin"].forEach(function (ev) {
    document.addEventListener(ev, function (e) { var r = e.target.closest && e.target.closest(".cvw-row[data-field]"); if (r) trace(r, true); });
  });
  ["mouseout", "focusout"].forEach(function (ev) {
    document.addEventListener(ev, function (e) { var r = e.target.closest && e.target.closest(".cvw-row[data-field]"); if (r) trace(r, false); });
  });

  // ---- live proof: three-lane sequence driven by real prover events ----
  var job = document.getElementById("job");
  if (!job) return;
  var id = job.getAttribute("data-job");
  var list = document.getElementById("stages");
  var seen = 0, done = false;
  var labels = { running: "Running", done: "Finished", failed: "Failed", cancelled: "Cancelled" };
  // lane geometry: b = bank (col 1), i = institution (col 2), v = verifier (col 3)
  var SHAPE = {
    started: ["dot i", function () { return "Prover starts on the institution's side"; }],
    verifier_connect: ["arrow i-v", function () { return "Connects to AICTE's verifier"; }],
    challenge_accepted: ["arrow i-v back", function () { return "Challenge accepted and consumed"; }],
    bank_connect: ["arrow b-i back", function (d) { return "Opens a TLS connection to " + d; }],
    mpc_tls: ["arrow b-i dual", function () { return "One TLS session; its keys are split between the institution and the verifier"; }],
    response: ["arrow b-i", function (d) { return "Bank sends " + d.bytes + " bytes, encrypted"; }],
    select: ["dot i", function (d) { return d.warning ? d.warning : "Chooses what to disclose: " + (d.disclosure === "body" ? "the deposit page (it holds only this deposit)" : "this deposit's record only"); }],
    prove: ["arrow i-v", function () { return "Selective-disclosure proof"; }],
    proved: ["dot v", function (d) { return "Transcript verified: HTTP " + d.http_status + ", " + d.response_bytes + "-byte response"; }],
    submitted: ["dot v", function () { return "Facts handed to the register for matching"; }],
    aborted: ["dot i bad", function (d) { return "Stopped: " + d; }]
  };
  function row(st) {
    var shape = SHAPE[st.stage] || ["dot i", function () { return st.text; }];
    var li = document.createElement("li");
    li.className = "row new " + shape[0];
    var text;
    try { text = shape[1](st.detail); } catch (e) { text = st.text; }
    li.innerHTML = "<span class='msg'>" + esc(text) + "</span><span class='t'>" + (st.t_ms / 1000).toFixed(2) + " s</span>";
    return li;
  }
  function finish(j) {
    done = true;
    document.getElementById("job-spin").hidden = true;
    document.getElementById("job-wait").hidden = true;
    document.getElementById("cancel-form").hidden = true;
    var body = document.getElementById("job-result-body");
    var acts = document.getElementById("job-actions");
    if (j.evidence_id) {
      body.className = "";
      body.innerHTML = "<span class='stamp stamp-" + esc(j.decision) + " fresh'>" + esc(j.decision_label) + "</span>";
      acts.innerHTML = (j.reason ? "<p class='small muted'>" + esc(j.reason) + "</p>" : "") +
        (j.error ? "<p class='small muted'>" + esc(j.error) + "</p>" : "") +
        "<p><a class='btn btn-solid' href='/evidence/" + j.evidence_id + "'>Open the evidence</a></p>";
      fetch("/evidence/" + j.evidence_id + "/receipt", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.text() : ""; }).then(function (h) {
        if (!h.trim()) return;
        var box = document.getElementById("job-receipt");
        box.innerHTML = "<div class='sec-head'><h2>What AICTE actually received</h2></div>" + h;
        box.hidden = false;
      });
    } else {
      body.className = "st st-rejected";
      body.textContent = labels[j.state];
      acts.innerHTML = "<div class='note note-bad'>" + esc(j.error || "No result.") + "</div>" +
        "<p style='margin-top:14px'><a class='btn btn-quiet' href='/challenges/" + esc(j.challenge_id) + "'>Back to the request</a></p>";
    }
  }
  function tick() {
    fetch("/api/jobs/" + id, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (j) {
        for (; seen < j.stages.length; seen++) list.appendChild(row(j.stages[seen]));
        document.getElementById("job-state").textContent = labels[j.state] || j.state;
        if (j.state !== "running") { finish(j); return; }
        setTimeout(tick, 400);
      })
      .catch(function (err) {
        var w = document.getElementById("job-wait");
        w.hidden = false;
        w.textContent = "Lost contact with the portal (" + err.message + "). Retrying…";
        if (!done) setTimeout(tick, 2000);
      });
  }
  tick();
})();
