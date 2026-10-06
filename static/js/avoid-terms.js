// "Avoid in Job Postings" on the Dashboard: work you can't or don't want to do, kept as chips like Skills and sent
// with the profile form as one comma-separated field.
(function () {
  const field = document.getElementById("avoid-terms-field");
  const list = document.getElementById("avoid-term-list");
  const input = document.getElementById("new-avoid-term");
  const add = document.getElementById("add-avoid-term");
  if (!field || !list || !input || !add) return;
  const terms = field.value.split(",").map((term) => term.trim()).filter(Boolean);
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  // On the Search page the list saves itself; on the Dashboard it goes with the profile form.
  async function save() {
    if (!field.dataset.autosave) return;
    try {
      const response = await fetch("/avoid-terms", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ terms }),
      });
      if (!response.ok) throw new Error();
    } catch {
      window.showToast?.("Could not save the Ignore list.", "danger");
    }
  }

  function render() {
    list.replaceChildren();
    terms.forEach((term, index) => {
      const chip = document.createElement("span");
      chip.className = "skill-chip";
      const label = document.createElement("span");
      label.textContent = term;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Remove ${term}`);
      remove.addEventListener("click", () => { terms.splice(index, 1); render(); save(); });
      chip.append(label, remove);
      list.append(chip);
    });
    field.value = terms.join(", ");
  }

  // Typing "motion, video editing" adds both.
  function addTyped() {
    input.value.split(",").map((term) => term.trim()).filter(Boolean).forEach((term) => {
      if (!terms.some((have) => have.toLowerCase() === term.toLowerCase())) terms.push(term);
    });
    input.value = "";
    render();
    save();
  }

  add.addEventListener("click", addTyped);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); addTyped(); }
  });
  render();
})();
