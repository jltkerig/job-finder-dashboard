// Job title help, matching Job Finder: a type-ahead list on any box with data-title-suggest
// ("single", or "list" for comma-separated titles), and suggested titles under the box marked
// data-title-chips while the user isn't typing in it.
(function () {
  "use strict";

  function attach(input) {
    var multi = input.dataset.titleSuggest === "list";
    var box = document.createElement("ul");
    box.className = "title-suggest"; box.hidden = true; box.setAttribute("role", "listbox");
    box.id = (input.id || input.name) + "-suggest";
    input.setAttribute("autocomplete", "off"); input.setAttribute("aria-autocomplete", "list"); input.setAttribute("aria-controls", box.id);
    input.insertAdjacentElement("afterend", box);
    input.parentElement.classList.add("title-suggest-host");
    var items = [], active = -1, timer = null, asked = "";

    function term() { return (multi ? input.value.split(",").pop() : input.value).trim(); }
    function render() {
      box.replaceChildren.apply(box, items.map(function (title, i) {
        var li = document.createElement("li");
        li.id = box.id + "-" + i; li.setAttribute("role", "option"); li.textContent = title;
        if (i === active) { li.className = "active"; li.setAttribute("aria-selected", "true"); }
        li.addEventListener("mousedown", function (event) { event.preventDefault(); choose(title); });
        return li;
      }));
      box.hidden = !items.length;
      input.setAttribute("aria-expanded", String(!box.hidden));
      if (active >= 0) input.setAttribute("aria-activedescendant", box.id + "-" + active); else input.removeAttribute("aria-activedescendant");
    }
    function choose(title) {
      if (multi) {
        var parts = input.value.split(",").map(function (p) { return p.trim(); });
        parts[parts.length - 1] = title;
        input.value = parts.filter(Boolean).join(", ") + ", ";
      } else input.value = title;
      items = []; active = -1; render();
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
    function load() {
      var typed = term();
      if (typed.length < 2) { items = []; render(); return; }
      if (typed === asked) return;
      asked = typed;
      fetch("/job-title-matches?q=" + encodeURIComponent(typed), { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (term() !== typed) return;
          var earlier = multi ? input.value.split(",").slice(0, -1).map(function (t) { return t.trim().toLowerCase(); }) : [];
          items = (data.matches || []).filter(function (t) { return t.toLowerCase() !== typed.toLowerCase() && earlier.indexOf(t.toLowerCase()) < 0; });
          active = -1; render();
        })
        .catch(function () { items = []; render(); });
    }
    input.addEventListener("input", function () { clearTimeout(timer); asked = ""; timer = setTimeout(load, 150); });
    input.addEventListener("keydown", function (event) {
      if (box.hidden) return;
      if (event.key === "ArrowDown") { event.preventDefault(); active = (active + 1) % items.length; render(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); active = (active - 1 + items.length) % items.length; render(); }
      else if (event.key === "Enter" && active >= 0) { event.preventDefault(); choose(items[active]); }
      else if (event.key === "Escape") { items = []; render(); }
    });
    input.addEventListener("blur", function () { setTimeout(function () { items = []; render(); }, 120); });
  }

  function chips(holder) {
    var target = document.getElementById(holder.dataset.titleChips);
    var also = holder.dataset.alsoFrom ? document.getElementById(holder.dataset.alsoFrom) : null;
    var row = holder.querySelector(".chip-row"), timer = null;
    if (!target) return;
    function refresh() {
      var current = [also ? also.value : ""].concat(target.value.split(",")).map(function (t) { return t.trim(); }).filter(Boolean);
      var typing = document.activeElement === target && target.value.split(",").pop().trim();
      if (!current.length || typing) { holder.hidden = true; return; }
      fetch("/job-title-suggestions?titles=" + encodeURIComponent(current.join(", ")), { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          row.replaceChildren.apply(row, (data.suggestions || []).map(function (title) {
            var b = document.createElement("button");
            b.type = "button"; b.className = "suggest-chip"; b.textContent = "+ " + title;
            b.addEventListener("click", function () {
              var kept = target.value.split(",").map(function (t) { return t.trim(); }).filter(Boolean);
              target.value = kept.concat([title]).join(", ");
              refresh();
            });
            return b;
          }));
          holder.hidden = !row.children.length;
        })
        .catch(function () { holder.hidden = true; });
    }
    function queue() { clearTimeout(timer); timer = setTimeout(refresh, 400); }
    target.addEventListener("input", queue); target.addEventListener("blur", queue);
    if (also) { also.addEventListener("change", queue); also.addEventListener("blur", queue); }
    refresh();
  }

  document.querySelectorAll("[data-title-suggest]").forEach(attach);
  document.querySelectorAll("[data-title-chips]").forEach(chips);
})();
