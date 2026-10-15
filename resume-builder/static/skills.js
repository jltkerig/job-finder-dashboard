// Suggested skills under the Profile's Skills box (data-skill-chips = the box's id, data-title-from = the primary title box).
// Click one to add it to the box; the list refreshes as the skills change.
(function () {
  "use strict";
  document.querySelectorAll("[data-skill-chips]").forEach(function (holder) {
    var target = document.getElementById(holder.dataset.skillChips);
    var title = holder.dataset.titleFrom ? document.getElementById(holder.dataset.titleFrom) : null;
    var row = holder.querySelector(".chip-row"), timer = null;
    if (!target) return;
    function refresh() {
      var query = "skills=" + encodeURIComponent(target.value) + "&title=" + encodeURIComponent(title ? title.value : "");
      fetch("/skill-suggestions?" + query, { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          row.replaceChildren.apply(row, (data.suggestions || []).map(function (skill) {
            var b = document.createElement("button");
            b.type = "button"; b.className = "suggest-chip"; b.textContent = "+ " + skill;
            b.addEventListener("click", function () {
              var kept = target.value.split(",").map(function (t) { return t.trim(); }).filter(Boolean);
              target.value = kept.concat([skill]).join(", ");
              refresh();
            });
            return b;
          }));
          holder.hidden = !row.children.length;
        })
        .catch(function () { holder.hidden = true; });
    }
    function queue() { clearTimeout(timer); timer = setTimeout(refresh, 500); }
    target.addEventListener("input", queue); target.addEventListener("blur", queue);
    if (title) title.addEventListener("change", queue);
    refresh();
  });
})();
