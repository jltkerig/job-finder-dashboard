// Dashboard Apply queue: Mark Applied sets the job to Applied; Remove takes it off the queue (it stays saved).
(() => {
  const section = document.getElementById("apply-queue");
  if (!section) return;
  const status = document.getElementById("apply-queue-status");
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  async function send(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify(body || {}),
    });
    if (!response.ok) throw new Error();
  }

  section.addEventListener("click", async (event) => {
    const button = event.target.closest(".apply-queue-applied, .apply-queue-remove");
    if (!button) return;
    const item = button.closest(".apply-queue-item");
    const id = item.dataset.companyId;
    button.disabled = true;
    try {
      if (button.classList.contains("apply-queue-applied")) await send(`/apply-queue/applied/${id}`);
      else await send("/apply-queue", { company_ids: [id], queued: false });
      item.remove();
      status.textContent = button.classList.contains("apply-queue-applied") ? "Marked Applied." : "Removed from the queue.";
      if (!section.querySelector(".apply-queue-item")) section.hidden = true;
    } catch {
      status.textContent = "Could not save that. Try again.";
      button.disabled = false;
    }
  });
})();
