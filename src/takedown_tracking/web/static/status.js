// Victim status page. All text is inserted with textContent (never innerHTML).
(function () {
  "use strict";

  var form = document.getElementById("lookup");
  var button = document.getElementById("submit");
  var errorBox = document.getElementById("error");
  var result = document.getElementById("result");

  var TONE = {
    removal_confirmed: "good",
    platform_says_removed: "info",
    platform_acknowledged: "info",
    sent_to_platform: "info",
    needs_police_action: "warn",
    platform_declined: "bad",
    still_online_despite_claim: "bad"
  };

  function set(id, text) { document.getElementById(id).textContent = text; }

  function when(iso) {
    if (!iso) { return "-"; }
    var d = new Date(iso);
    return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }

  function duration(hours) {
    if (hours === null || hours === undefined) { return "-"; }
    if (hours < 1) { return Math.round(hours * 60) + " minutes"; }
    if (hours < 48) { return hours.toFixed(1) + " hours"; }
    return (hours / 24).toFixed(1) + " days";
  }

  function showError(text) {
    errorBox.textContent = text;
    errorBox.hidden = false;
    result.hidden = true;
  }

  function render(s) {
    var status = document.getElementById("status");
    status.textContent = s.message;
    status.className = "status " + (TONE[s.stage] || "info");

    set("r-ref", s.reference);
    set("r-sent", when(s.sent_at));

    var deadline = s.legal_deadline_hours + " hours (by " + when(s.deadline_at) + ")";
    if (s.within_deadline === true) { deadline += " - met"; }
    if (s.within_deadline === false) { deadline += " - missed"; }
    set("r-deadline", deadline);

    if (s.removed_at) {
      set("r-time-label", "Time taken to remove");
      set("r-time", duration(s.hours_taken) + (s.removal_confirmed_independently ? " (confirmed)" : " (platform's report)"));
    } else {
      set("r-time-label", "Time so far");
      set("r-time", duration(s.hours_elapsed) + " and counting");
    }

    var check = "Not checked yet";
    if (s.independent_check) {
      var r = s.independent_check.result;
      check = (r === "removed" ? "Content not found" : r === "online" ? "Content still online" : "Could not check")
        + " (last checked " + when(s.independent_check.last_checked_at) + ")";
    }
    set("r-check", check);
    set("r-copy", s.copy_found_at ? "A copy was found on " + when(s.copy_found_at) + " and flagged" : "None found");

    errorBox.hidden = true;
    result.hidden = false;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var reference = form.reference.value.trim();
    var code = form.code.value.trim();
    if (!reference || !code) {
      showError("Enter both the reference number and the access code.");
      return;
    }
    button.disabled = true;
    fetch("/api/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reference: reference, code: code }),
      cache: "no-store",
      credentials: "omit"
    })
      .then(function (res) {
        return res.json().then(function (body) { return { ok: res.ok, body: body }; });
      })
      .then(function (r) {
        if (r.ok) { render(r.body); } else { showError(r.body.error || "Something went wrong."); }
      })
      .catch(function () { showError("Could not reach the server. Please try again."); })
      .then(function () {
        button.disabled = false;
        form.code.value = "";
      });
  });
})();
