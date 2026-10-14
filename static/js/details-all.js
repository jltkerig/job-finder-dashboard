// Open All Details / Close All Details, in the Search page's filter bar: drops down (or folds up) the Details of every
// result row shown and of every Top 10 pick at once. A posting is read as its Details opens, so they open a moment apart.
(() => {
  const button = document.getElementById("details-all-button");
  if (!button) return;
  const GAP_MS = 150;
  let pending = 0;      // Details still to be clicked from the last press
  let lastOpening = false;
  let run = 0;          // a newer press cancels one that is still going

  const shown = (element) => element.getClientRects().length > 0;

  // Every Details button on screen (a filtered-out row or a closed Top 10 panel has none), and whether its panel is open.
  function toggles() {
    const items = [];
    document.querySelectorAll("#results-table .details-action").forEach((el) => {
      const target = document.getElementById(el.dataset.detailsTarget);
      if (target && shown(el)) items.push({ el, open: !target.hidden });
    });
    document.querySelectorAll("#top-picks-list .top-pick-details-button").forEach((el) => {
      if (shown(el)) items.push({ el, open: el.getAttribute("aria-expanded") === "true" });
    });
    return items;
  }

  const allOpen = (items) => items.length > 0 && items.every((item) => item.open);

  function label(opening) {
    button.textContent = opening ? "Close All Details" : "Open All Details";
  }

  button.addEventListener("click", () => {
    const opening = pending > 0 ? !lastOpening : !allOpen(toggles());
    const todo = toggles().filter((item) => item.open !== opening).map((item) => item.el);
    const mine = ++run;
    lastOpening = opening;
    pending = todo.length;
    label(opening);
    todo.forEach((el, index) => setTimeout(() => {
      if (mine !== run) return;
      el.click();
      pending -= 1;
    }, index * GAP_MS));
  });

  // Opening or closing one by hand changes what the button should offer next.
  document.addEventListener("click", (event) => {
    if (pending > 0 || !event.target.closest(".details-action, .top-pick-details-button")) return;
    setTimeout(() => label(allOpen(toggles())), 0);
  });
})();
