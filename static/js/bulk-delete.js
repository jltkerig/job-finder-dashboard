// Dashboard: tick saved jobs (or "Select all shown", which follows the current filter) and delete them together
// after an "Are you sure?" prompt.
(() => {
  const bar = document.getElementById("bulk-bar");
  if (!bar) return;
  const all = document.getElementById("bulk-all");
  const count = document.getElementById("bulk-count");
  const remove = document.getElementById("bulk-delete");
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const boxes = () => [...document.querySelectorAll(".bulk-check")];
  const chosen = () => boxes().filter((box) => box.checked);

  function update() {
    const picked = chosen().length;
    const total = boxes().length;
    count.textContent = picked ? `${picked} selected` : "None selected";
    remove.disabled = picked === 0;
    all.checked = total > 0 && picked === total;
    all.indeterminate = picked > 0 && picked < total;
  }

  all.addEventListener("change", () => {
    boxes().forEach((box) => { box.checked = all.checked; });
    update();
  });
  document.addEventListener("change", (event) => {
    if (event.target.classList.contains("bulk-check")) update();
  });

  remove.addEventListener("click", async () => {
    const ids = chosen().map((box) => Number(box.value));
    if (!ids.length) return;
    const jobs = ids.length === 1 ? "this saved job" : `these ${ids.length} saved jobs`;
    if (!window.confirm(`Are you sure? This permanently deletes ${jobs}. This cannot be undone.`)) return;
    remove.disabled = true;
    try {
      const response = await fetch("/delete-kept-many", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ ids }),
      });
      if (!response.ok) throw new Error();
      window.location.reload();
    } catch {
      count.textContent = "Could not delete those jobs. Try again.";
      remove.disabled = false;
    }
  });

  update();
})();
