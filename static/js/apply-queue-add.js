// Dashboard: add saved jobs to the Apply Queue by ticking them in a list (the same queue the Apply buttons fill).
(() => {
  const panel = document.getElementById("queue-add");
  if (!panel) return;
  const list = document.getElementById("queue-add-list"), search = document.getElementById("queue-add-search");
  const add = document.getElementById("queue-add-button"), status = document.getElementById("queue-add-status");
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  let jobs = [];

  function collect() {
    const queued = new Set([...document.querySelectorAll(".apply-queue-item")].map((li) => li.dataset.companyId));
    jobs = [...document.querySelectorAll(".saved-job-card")].map((card) => ({
      id: card.dataset.companyId,
      title: card.querySelector(".summary-title strong")?.textContent.trim() || "Job",
      company: card.querySelector(".summary-company")?.textContent.trim() || "",
      state: card.querySelector(".application-indicator")?.textContent.trim() || "",
    })).filter((job) => job.id && !queued.has(job.id) && /^not applied$/i.test(job.state));
  }
  function ticked() { return [...list.querySelectorAll("input:checked")].map((box) => box.value); }
  function refresh() { const n = ticked().length; add.disabled = !n; add.textContent = n ? `Add ${n} to the Queue` : "Add Selected to the Queue"; }
  function render() {
    const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
    const keep = new Set(ticked());
    const shown = jobs.filter((job) => words.every((word) => `${job.title} ${job.company}`.toLowerCase().includes(word)));
    list.replaceChildren(...shown.map((job) => {
      const li = document.createElement("li"), label = document.createElement("label"), box = document.createElement("input");
      box.type = "checkbox"; box.value = job.id; box.checked = keep.has(job.id); box.addEventListener("change", refresh);
      const text = document.createElement("span"); text.innerHTML = "<strong></strong> <small></small>";
      text.querySelector("strong").textContent = job.title; text.querySelector("small").textContent = job.company ? `at ${job.company}` : "";
      label.append(box, text); li.append(label); return li;
    }));
    if (!shown.length) { const li = document.createElement("li"); li.className = "queue-add-none"; li.textContent = jobs.length ? "No saved job matches that." : "All your saved jobs are already queued or applied to."; list.append(li); }
    refresh();
  }
  panel.addEventListener("toggle", () => { if (panel.open) { collect(); render(); } });
  search.addEventListener("input", render);
  add.addEventListener("click", async () => {
    const ids = ticked(); if (!ids.length) return;
    add.disabled = true; status.textContent = "Adding…";
    try {
      const response = await fetch("/apply-queue", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify({ company_ids: ids, queued: true }) });
      if (!response.ok) throw new Error();
      status.textContent = "Added. Refreshing…";
      window.location.hash = "apply-queue"; window.location.reload();
    } catch { status.textContent = "Couldn't add them. Try again."; add.disabled = false; }
  });

  // A pasted link: read the posting, save it as a job on the Dashboard and queue it.
  const linkBox = document.getElementById("queue-link"), linkButton = document.getElementById("queue-link-button"), linkStatus = document.getElementById("queue-link-status");
  linkButton?.addEventListener("click", async () => {
    const url = linkBox.value.trim(); if (!url) { linkStatus.textContent = "Paste a link first."; return; }
    linkButton.disabled = true; linkStatus.textContent = "Reading the posting…";
    try {
      const response = await fetch("/apply-queue/add-link", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify({ url }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.message || "That link didn't work.");
      linkStatus.textContent = `${data.already ? "Already saved. Queued" : "Added"}: ${[data.title, data.company].filter(Boolean).join(" at ")}. Refreshing…`;
      window.location.hash = "apply-queue"; window.location.reload();
    } catch (error) { linkStatus.textContent = error.message || "Couldn't add that link."; linkButton.disabled = false; }
  });
  linkBox?.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); linkButton.click(); } });
})();
