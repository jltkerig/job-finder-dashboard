// "Make Them From a Job Link": read the posting at a pasted link, show what was found (title, company and text can be
// corrected), then copy a ready request for Claude Desktop and open it.
(function () {
  "use strict";
  var form = document.getElementById("job-link-form");
  if (!form) return;
  var found = document.getElementById("job-link-found"), status = document.getElementById("job-link-status");
  var title = document.getElementById("job-link-title"), company = document.getElementById("job-link-company");
  var text = document.getElementById("job-link-text"), note = document.getElementById("job-link-note"), size = document.getElementById("job-link-size");
  var readButton = document.getElementById("job-link-read"), copy = document.getElementById("job-link-copy");
  var started = "", poll = null, results = document.getElementById("job-link-results"), files = document.getElementById("job-link-files"), checkAgain = document.getElementById("job-link-check");
  var csrf = form.querySelector('input[name="csrf"]').value, link = "";

  function post(url, fields) {
    var body = new FormData(); body.append("csrf", csrf);
    Object.keys(fields).forEach(function (k) { body.append(k, fields[k]); });
    return fetch(url, { method: "POST", body: body, cache: "no-store" });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    found.hidden = true; status.textContent = "Reading the posting…"; readButton.disabled = true;
    post("/job-link/read", { url: document.getElementById("job-link-url").value })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.url) { status.textContent = data.note || "That doesn't look like a web address."; return; }
        link = data.url; title.value = data.title || ""; company.value = data.company || ""; text.value = data.text || "";
        note.textContent = data.note || ""; size.textContent = data.text ? "(" + data.text.length.toLocaleString() + " characters)" : "(none found)";
        found.hidden = false; status.textContent = data.text ? "Read it. Check the title and company, then copy the request." : "";
        text.dispatchEvent(new Event("input"));
      })
      .catch(function () { status.textContent = "The posting couldn't be read. Check the link and try again."; })
      .then(function () { readButton.disabled = false; });
  });

  copy.addEventListener("click", function () {
    copy.disabled = true;
    post("/job-link/request", { url: link, title: title.value, company: company.value, text: text.value })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var request = data.request || "";
        if (!request) { status.textContent = "Read a job first."; return; }
        started = data.started || ""; results.hidden = false; files.replaceChildren();
        var done = function () { status.textContent = "Request copied. Paste it in Claude Desktop and press Enter; your files will show up below."; window.location.href = "claude://"; watch(); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(request).then(done, function () { window.prompt("Copy this request, then paste it into Claude Desktop:", request); });
        else window.prompt("Copy this request, then paste it into Claude Desktop:", request);
      })
      .catch(function () { status.textContent = "Couldn't build the request. Try again."; })
      .then(function () { copy.disabled = false; });
  });

  // After the request is sent, look every few seconds for the résumé and cover letter Claude Desktop saves, and show download links.
  function show(list) {
    files.replaceChildren.apply(files, list.map(function (f) {
      var li = document.createElement("li"), a = document.createElement("a");
      a.href = f.url; a.className = "bordered-button primary"; a.textContent = "Download";
      var label = document.createElement("span");
      label.textContent = (f.kind === "resume" ? "Résumé" : "Cover letter") + ": " + f.pdf;
      li.append(a, label); return li;
    }));
  }
  function check() {
    if (!started) return Promise.resolve(false);
    return fetch("/job-link/files?since=" + encodeURIComponent(started), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        show(data.files || []);
        var kinds = (data.files || []).map(function (f) { return f.kind; });
        var both = kinds.indexOf("resume") >= 0 && kinds.indexOf("cover_letter") >= 0;
        if (both) { status.textContent = "Done. Your résumé and cover letter are ready to download."; clearInterval(poll); poll = null; }
        else if (kinds.length) status.textContent = "Part of it is ready. Waiting for the rest…";
        return both;
      })
      .catch(function () { return false; });
  }
  function watch() {
    clearInterval(poll); var tries = 0;
    poll = setInterval(function () { if (++tries > 225) { clearInterval(poll); poll = null; return; } check(); }, 4000);
  }
  checkAgain.addEventListener("click", function () { check().then(function () { if (!poll && started) watch(); }); });
})();
