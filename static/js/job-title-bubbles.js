// Search page: the job titles are bubbles under a box (like the cities), not one long box. The real value stays in the
// hidden #job-title input as "Title, Title" (charts.js reads and saves it); this script shows it as bubbles and adds to it.
(() => {
  const store = document.getElementById("job-title");
  const entry = document.getElementById("job-title-add");
  const list = document.getElementById("job-title-list");
  const addButton = document.getElementById("add-job-title");
  if (!store || !entry || !list) return;

  const titles = () => store.value.split(",").map((part) => part.trim()).filter(Boolean);

  function save(next) {
    const seen = new Set();
    const clean = next.map((title) => title.trim()).filter((title) => title && !seen.has(title.toLowerCase()) && seen.add(title.toLowerCase()));
    store.value = clean.join(", ");
    try { localStorage.setItem("jobFinderJobTitle", store.value); } catch { /* private window */ }
    render();
    store.dispatchEvent(new Event("input", { bubbles: true })); // charts.js loads the related-title suggestions
  }

  function render() {
    list.replaceChildren(...titles().map((title) => {
      const chip = document.createElement("span");
      chip.className = "city-chip title-bubble";
      const text = document.createElement("span");
      text.textContent = title;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "city-remove";
      remove.setAttribute("aria-label", `Remove ${title}`);
      remove.textContent = "×";
      remove.addEventListener("click", () => save(titles().filter((other) => other !== title)));
      chip.append(text, remove);
      return chip;
    }));
  }

  function commit() {
    const typed = entry.value.split(",").map((part) => part.trim()).filter(Boolean);
    if (!typed.length) return;
    entry.value = "";
    save([...titles(), ...typed]);
  }

  addButton?.addEventListener("click", commit);
  entry.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !document.querySelector("#job-title-add-suggest li.active")) { event.preventDefault(); commit(); }
  });
  entry.addEventListener("change", commit); // a pick from the type-ahead list, or leaving the box with something typed
  // A related-title suggestion (the coloured bubbles) adds itself to the value in charts.js; show it as a bubble.
  document.getElementById("job-title-suggestions")?.addEventListener("click", () => requestAnimationFrame(render));
  // Whatever is typed is part of the search when it starts, refreshes or ranks.
  document.addEventListener("click", (event) => {
    if (event.target.closest("#start-search, #top-picks-button, #refresh-search")) commit();
  }, true);
  render();
})();
