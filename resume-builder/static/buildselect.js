// Saved Résumés and Cover Letters: tick files (or "Select all shown"), then Delete Selected after a confirmation.
(function () {
  "use strict";
  var form = document.getElementById("builds-form");
  if (!form) return;
  var all = document.getElementById("builds-select-all"), count = document.getElementById("builds-count"), remove = document.getElementById("builds-delete");
  function picks() { return Array.prototype.slice.call(form.querySelectorAll(".build-pick")); }
  function refresh() {
    var chosen = picks().filter(function (c) { return c.checked; }).length, total = picks().length;
    count.textContent = chosen ? chosen + " selected" : "None selected";
    remove.disabled = !chosen;
    all.checked = chosen > 0 && chosen === total;
    all.indeterminate = chosen > 0 && chosen < total;
    picks().forEach(function (c) { c.closest("tr").classList.toggle("is-picked", c.checked); });
  }
  all.addEventListener("change", function () { picks().forEach(function (c) { c.checked = all.checked; }); refresh(); });
  form.addEventListener("change", function (event) { if (event.target.classList.contains("build-pick")) refresh(); });
  form.addEventListener("submit", function (event) {
    var n = picks().filter(function (c) { return c.checked; }).length;
    if (!n || !window.confirm("Delete " + n + " selected file" + (n === 1 ? "" : "s") + "? A backup copy is saved first, but they will leave this list.")) event.preventDefault();
  });
  refresh();
})();
