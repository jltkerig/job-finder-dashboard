// Profile page helpers. First: Work History order. The form saves jobs in the order of the "pos" field in each job (the
// server sorts by it); Move up / Move down rewrite it and renumber the "Work history · Job 2 of 5" markers. The empty
// "Add a Job" box stays last. Second: the More links rows.
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

// Profile > More links: "Add another link" adds an empty row (up to 12 links); the form counts them on save.
(function () {
  "use strict";
  var button = document.getElementById("add-link");
  if (!button) return;
  var box = button.parentElement;
  var count = box.querySelector("input[name='link_count']");
  button.addEventListener("click", function () {
    var rows = box.querySelectorAll(".link-row");
    if (rows.length >= 12) { button.disabled = true; return; }
    var next = rows[rows.length - 1].cloneNode(true);
    var index = rows.length;
    var select = next.querySelector("select");
    var input = next.querySelector("input");
    select.name = "link" + index + "_type"; select.value = "";
    input.name = "link" + index + "_url"; input.value = "";
    rows[rows.length - 1].after(next);
    count.value = String(index + 1);
    input.focus();
    if (index + 1 >= 12) button.disabled = true;
  });
})();
