// Address suggestions under a job's Street box (data-address-suggest = that job's field-name prefix). Choosing one fills the
// street, city, state and ZIP boxes of the same job; nothing is filled until one is chosen.
(function () {
  "use strict";
  document.querySelectorAll("input[data-address-suggest]").forEach(function (input) {
    var prefix = input.dataset.addressSuggest, form = input.form;
    function part(name) { return form.elements[prefix + name]; }
    var box = document.createElement("ul");
    box.className = "title-suggest"; box.hidden = true; box.setAttribute("role", "listbox");
    input.setAttribute("autocomplete", "off");
    input.insertAdjacentElement("afterend", box);
    input.parentElement.classList.add("title-suggest-host");
    var items = [], active = -1, timer = null, asked = "";

    function render() {
      box.replaceChildren.apply(box, items.map(function (s, i) {
        var li = document.createElement("li"); li.setAttribute("role", "option"); li.textContent = s.address;
        if (i === active) li.className = "active";
        li.addEventListener("mousedown", function (event) { event.preventDefault(); choose(s); });
        return li;
      }));
      box.hidden = !items.length;
    }
    function choose(s) {
      input.value = s.street || s.address; asked = input.value.trim();
      ["city", "state", "zip"].forEach(function (name) { if (part(name)) part(name).value = s[name] || ""; });
      items = []; active = -1; render();
    }
    function ask() {
      var typed = input.value.trim();
      if (typed.length < 4 || typed === asked) return;
      asked = typed;
      var town = [part("city") && part("city").value.trim(), part("state") && part("state").value.trim()].filter(Boolean).join(", ") ||
        (part("zip") ? part("zip").value.trim() : "");
      fetch("/address-suggest?q=" + encodeURIComponent(typed) + "&place=" + encodeURIComponent(town), { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (input.value.trim() !== typed || document.activeElement !== input) return;
          items = data.suggestions || []; active = -1; render();
        })
        .catch(function () { items = []; render(); });
    }
    input.addEventListener("input", function () {
      clearTimeout(timer); asked = "";
      if (input.value.trim().length < 4) { items = []; render(); return; }
      timer = setTimeout(ask, 400);
    });
    input.addEventListener("keydown", function (event) {
      if (box.hidden) return;
      if (event.key === "ArrowDown") { event.preventDefault(); active = (active + 1) % items.length; render(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); active = (active - 1 + items.length) % items.length; render(); }
      else if (event.key === "Enter" && active >= 0) { event.preventDefault(); choose(items[active]); }
      else if (event.key === "Escape") { items = []; render(); }
    });
    input.addEventListener("blur", function () { clearTimeout(timer); setTimeout(function () { items = []; render(); }, 120); });
  });
})();
