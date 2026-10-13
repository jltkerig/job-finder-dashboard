// Dashboard: choosing the Applied status fills in today's date in the job's "Date applied" field. It is still a
// normal date field, so it can be changed before saving or edited again any time later.
(() => {
  function today() {
    const now = new Date();
    const pad = (value) => String(value).padStart(2, "0");
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  }

  document.addEventListener("change", (event) => {
    const select = event.target;
    if (!select.matches || !select.matches('.saved-job-editor select[name="application_status"]')) return;
    if (select.value !== "Applied") return;
    const date = select.closest(".saved-job-editor").querySelector('input[name="applied_on"]');
    if (date && !date.value) date.value = today();
  });
})();
