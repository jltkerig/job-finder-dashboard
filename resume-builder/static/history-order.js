// Profile > Work History: move a job up or down. The form saves jobs in the order the boxes are in, so the order is
// written into each job's hidden "pos" field (the server sorts by it) and the "Work history · Job 2 of 5" markers are
// renumbered. The empty "Add a Job" box stays last.
(function () {
  "use strict";
  var rows = Array.prototype.slice.call(document.querySelectorAll("#profile fieldset.history-row[data-job]"));
  if (!rows.length) return;

  function jobs() {
    return Array.prototype.slice.call(document.querySelectorAll("#profile fieldset.history-row[data-job]:not(.history-new)"));
  }

  function renumber() {
    var list = jobs();
    list.forEach(function (row, index) {
      row.querySelector("input.job-pos").value = String(index);
      var marker = row.previousElementSibling;
      if (marker && marker.classList.contains("section-marker")) {
        marker.querySelector(".marker-count").textContent = "Job " + (index + 1) + " of " + list.length;
      }
      row.querySelector(".job-up").disabled = index === 0;
      row.querySelector(".job-down").disabled = index === list.length - 1;
    });
  }

  function move(row, step) {
    var list = jobs();
    var target = list[list.indexOf(row) + step];
    if (!target) return;
    var marker = row.previousElementSibling;
    var targetMarker = target.previousElementSibling;
    // A job and its marker travel together.
    if (step < 0) {
      targetMarker.before(marker, row);
    } else {
      target.after(marker, row);
    }
    renumber();
    row.scrollIntoView({ block: "nearest" });
  }

  rows.forEach(function (row) {
    var up = row.querySelector(".job-up");
    var down = row.querySelector(".job-down");
    if (up) up.addEventListener("click", function () { move(row, -1); });
    if (down) down.addEventListener("click", function () { move(row, 1); });
  });
  renumber();
})();
