// Clear Results: after one confirmation, delete the listings you haven't saved, queued, applied to or rejected.
(() => {
  const button = document.getElementById("clear-results-button");
  if (!button) return;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  button.addEventListener("click", async () => {
    if (!window.confirm("Delete every result you haven't saved, queued or applied to? Rejected listings stay hidden. This can't be undone.")) return;
    button.disabled = true;
    try {
      const response = await fetch("/clear-results", { method: "POST", headers: { "X-CSRF-Token": csrfToken } });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || "Could not clear the results.");
      window.location.reload();
    } catch (error) {
      window.alert(error.message);
      button.disabled = false;
    }
  });
})();
