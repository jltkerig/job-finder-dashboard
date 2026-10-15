// "Job Link Résumé Creation": paste a link, press Create, and the résumé and cover letter are written automatically. The page
// shows progress and the download links as the files are saved. Nothing to copy or paste.
(function () {
  "use strict";
  var form = document.getElementById("job-link-form");
  if (!form) return;
  var status = document.getElementById("job-link-status"), button = document.getElementById("job-link-read");
  var results = document.getElementById("job-link-results"), files = document.getElementById("job-link-files");
  var csrf = form.querySelector('input[name="csrf"]').value, poll = null;

  function showFiles(list) {
    results.hidden = !list.length;
    files.replaceChildren.apply(files, list.map(function (f) {
      var li = document.createElement("li"), a = document.createElement("a"), label = document.createElement("span");
      a.href = f.url; a.className = "bordered-button primary"; a.textContent = "Download";
      label.textContent = (f.kind === "resume" ? "Résumé" : "Cover letter") + ": " + f.pdf;
      li.append(a, label); return li;
    }));
  }

  function watch(id, started) {
    clearInterval(poll);
    poll = setInterval(function () {
      fetch("/job-link/status?id=" + encodeURIComponent(id), { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { status.textContent = data.error; stop(); return; }
          showFiles(data.files || []);
          var mins = Math.floor((Date.now() - started) / 60000);
          if (data.state === "done") {
            status.textContent = data.files.length >= 2 ? "Done. Your résumé and cover letter are ready." : "Finished, but fewer files than expected were saved. " + (data.summary || "");
            stop();
          } else if (data.state === "failed") {
            status.textContent = "It didn't finish: " + (data.message || "unknown problem") + (data.files.length ? " What was saved is below." : "");
            stop();
          } else status.textContent = data.message + (mins ? " (" + mins + " min so far)" : "");
        })
        .catch(function () { /* try again on the next tick */ });
    }, 3000);
  }
  function stop() { clearInterval(poll); poll = null; button.disabled = false; }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    button.disabled = true; showFiles([]); status.textContent = "Reading the posting…";
    var body = new FormData(); body.append("csrf", csrf); body.append("url", document.getElementById("job-link-url").value);
    fetch("/job-link/make", { method: "POST", body: body, cache: "no-store" })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) { status.textContent = res.d.error || "That link didn't work."; button.disabled = false; return; }
        var label = [res.d.title, res.d.company].filter(Boolean).join(" at ");
        status.textContent = (label ? "Read: " + label + ". " : "Read the posting. ") + "Claude is writing now…" + (res.d.note ? " " + res.d.note : "");
        watch(res.d.id, Date.now());
      })
      .catch(function () { status.textContent = "Couldn't reach Résumé Builder. Try again."; button.disabled = false; });
  });
})();
