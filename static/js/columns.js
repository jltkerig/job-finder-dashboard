// The Columns row in the results filters: tick or untick a column to show or hide it. The choice is remembered
// in this browser. The Job column always shows, so it isn't in the list.
(() => {
  "use strict";
  const table = document.getElementById("results-table");
  const chooser = document.getElementById("column-chooser");
  if (!table || !chooser) return;
  const options = chooser.querySelector(".column-options");
  const showAll = document.getElementById("column-show-all");
  const STORAGE_KEY = "jobFinderHiddenColumns";
  const ALWAYS = new Set(["Job"]);
  const headers = [...table.tHead.rows[0].cells].map((cell, index) => ({ name: cell.textContent.trim(), index: index + 1 }));

  // One stylesheet rule per hidden column hides its header and its cells (the details rows span every column).
  const style = document.createElement("style");
  document.head.appendChild(style);

  function readHidden() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      return new Set(Array.isArray(saved) ? saved : []);
    } catch {
      return new Set();
    }
  }

  function saveHidden(hidden) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify([...hidden])); } catch { /* the choice lasts until reload */ }
  }

  function apply(hidden) {
    style.textContent = headers
      .filter((column) => hidden.has(column.name) && !ALWAYS.has(column.name))
      .map((column) => `#results-table > thead > tr > th:nth-child(${column.index}),`
        + ` #results-table > tbody > tr.result-row > td:nth-child(${column.index}) { display: none; }`)
      .join("\n");
    for (const box of options.querySelectorAll("input")) box.checked = !hidden.has(box.value);
  }

  let hidden = readHidden();
  for (const column of headers) {
    if (!column.name || ALWAYS.has(column.name)) continue; // the Job column can't be hidden, so it isn't listed
    const label = document.createElement("label");
    label.className = "column-option";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = column.name;
    box.addEventListener("change", () => {
      if (box.checked) hidden.delete(column.name);
      else hidden.add(column.name);
      saveHidden(hidden);
      apply(hidden);
    });
    label.append(box, ` ${column.name}`);
    options.appendChild(label);
  }
  showAll.addEventListener("click", () => {
    hidden = new Set();
    saveHidden(hidden);
    apply(hidden);
  });
  apply(hidden);
})();
