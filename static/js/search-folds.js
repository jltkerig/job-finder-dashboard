// Search for Jobs: each part (Job Titles, City/Zip/Country/State, Ignore) folds away. A closed row still says what is
// inside: how many, and the first few. Open or closed is remembered in this browser; Job Titles starts open.
(function () {
  "use strict";
  const folds = document.querySelectorAll("details.search-fold");
  if (!folds.length) return;

  function entries(list) {
    return Array.from(list.children).map((item) => {
      const copy = item.cloneNode(true);
      copy.querySelectorAll("button, .visually-hidden").forEach((node) => node.remove());
      return copy.textContent.replace(/\s+/g, " ").trim();
    }).filter(Boolean);
  }

  folds.forEach((fold) => {
    const list = fold.querySelector(fold.dataset.list);
    const count = fold.querySelector(".fold-count");
    const glance = fold.querySelector(".fold-glance");
    const key = "jobFinder.searchFold." + fold.id;
    try {
      const saved = localStorage.getItem(key);
      if (saved !== null) fold.open = saved === "1";
    } catch (error) { /* remembering is a convenience */ }
    fold.addEventListener("toggle", () => {
      try { localStorage.setItem(key, fold.open ? "1" : "0"); } catch (error) { /* ok */ }
    });
    function refresh() {
      const items = entries(list);
      count.textContent = items.length ? String(items.length) : "";
      const shown = items.slice(0, 3).join(", ").replace(/\s*·\s*/g, " · ");
      glance.textContent = items.length ? shown + (items.length > 3 ? " and " + (items.length - 3) + " more" : "") : (fold.dataset.empty || "");
    }
    new MutationObserver(refresh).observe(list, { childList: true, subtree: true, characterData: true });
    refresh();
  });
})();
