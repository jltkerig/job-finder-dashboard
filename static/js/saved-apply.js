// Dashboard: "Apply" on a saved job adds it to the Apply Queue at the top of the page, like Apply on the Search page.
(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  document.addEventListener("click", async (event) => {
    const button = event.target.closest(".saved-apply-button");
    if (!button) return;
    event.preventDefault();
    button.disabled = true;
    try {
      const response = await fetch("/apply-queue", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ company_ids: [button.dataset.companyId], queued: true }),
      });
      if (!response.ok) throw new Error();
      // The queue is drawn by the server, so reload to show the job there (the page returns to the top).
      window.location.assign(`${window.location.pathname}${window.location.search}#apply-queue`);
      window.location.reload();
    } catch {
      button.disabled = false;
      button.textContent = "Try again";
    }
  });
})();
