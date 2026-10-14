// Dashboard Apply queue: Mark Applied sets the job to Applied; Remove takes it off the queue (it stays saved).
(() => {
  const section = document.getElementById("apply-queue");
  if (!section) return;
  const status = document.getElementById("apply-queue-status");
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  // Keep the Job Apply Stats box beside the profile and the heading count in step without a reload.
  function bump(name, by) {
    const cell = document.querySelector(`[data-stat="${name}"]`);
    if (cell) cell.textContent = Math.max(0, (Number(cell.textContent) || 0) + by);
  }
  function refreshCount() {
    const count = section.querySelectorAll(".apply-queue-item").length;
    const label = document.querySelector("#apply-queue-heading .field-help");
    if (label) label.textContent = `(${count})`;
    if (!count) section.hidden = true;
  }
  // Rejecting (or marking Closed) a saved job elsewhere on the page takes it off the queue too (static/js/charts.js).
  window.ApplyQueueDrop = (ids) => {
    new Set((ids || []).map(String)).forEach((id) => {
      const item = section.querySelector(`.apply-queue-item[data-company-id="${id}"]`);
      if (!item) return;
      item.remove();
      bump("queue", -1);
    });
    refreshCount();
  };

  async function send(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify(body || {}),
    });
    if (!response.ok) throw new Error();
    return response.json().catch(() => ({}));
  }

  // Combine puts the résumé and cover letter into one PDF (résumé first) and adds a "Combined" link beside them.
  section.addEventListener("click", async (event) => {
    const button = event.target.closest(".apply-queue-combine");
    if (!button) return;
    const item = button.closest(".apply-queue-item");
    button.disabled = true;
    status.textContent = "Combining the résumé and cover letter…";
    try {
      const response = await fetch(`/apply-queue/combine/${item.dataset.companyId}`, {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.pdf) throw new Error(data.message || "");
      let link = item.querySelector(".apply-queue-combined-link");
      if (!link) {
        link = document.createElement("a");
        link.className = "apply-queue-combined-link";
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = data.label || "Combined";
        button.before(link);
      }
      link.href = `/user-builds/${data.pdf}`;
      button.textContent = "Combine again";
      if (!item.querySelector(".apply-queue-download-combined")) {
        const save = document.createElement("button");
        save.type = "button";
        save.className = "bordered-button apply-queue-download-combined";
        save.title = "Save the combined résumé and cover letter as one PDF";
        save.textContent = "Download Combined";
        button.after(save);
      }
      status.textContent = "Combined into one PDF. Use Download Combined to save it, or the Combined link to open it.";
    } catch (error) {
      status.textContent = error.message || "Could not combine the files. Try again.";
    } finally {
      button.disabled = false;
    }
  });

  // Download saves the job's résumé and cover letter PDFs (the links shown under the job) with a Save As prompt.
  // A browser allows one prompt per click, so each click saves the next file. Browsers without the Save As
  // prompt get a normal download (the server sends the PDF as an attachment, so it never opens in a tab).
  const pending = new WeakMap();

  async function saveWithPrompt(link) {
    const name = decodeURIComponent(link.pathname.split("/").pop());
    if (!window.showSaveFilePicker) return false;
    let handle;
    try {
      handle = await window.showSaveFilePicker({ suggestedName: name, types: [{ description: "PDF", accept: { "application/pdf": [".pdf"] } }] });
    } catch (error) {
      if (error.name === "AbortError") return null;
      return false;
    }
    const response = await fetch(link.pathname);
    if (!response.ok) throw new Error();
    const writable = await handle.createWritable();
    await writable.write(await response.blob());
    await writable.close();
    return true;
  }

  // Job Finder (running on this computer) shows the system Save As window for each file. Null when it can't.
  async function saveViaApp(names) {
    try {
      const response = await fetch("/user-builds/save-as", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ names }),
      });
      if (!response.ok) return null;
      return await response.json();
    } catch {
      return null;
    }
  }

  function saveAsDownload(link) {
    const save = document.createElement("a");
    save.href = `${link.pathname}?download=1`;
    save.download = decodeURIComponent(link.pathname.split("/").pop());
    document.body.append(save);
    save.click();
    save.remove();
  }

  section.addEventListener("click", async (event) => {
    const button = event.target.closest(".apply-queue-download");
    if (!button) return;
    const label = pending.has(button) ? null : button.textContent;
    if (!pending.has(button)) {
      const links = [...button.closest(".apply-queue-item").querySelectorAll(".apply-queue-files a")];
      pending.set(button, { links, label });
    }
    const state = pending.get(button);
    const link = state.links[0];
    if (!link) return;
    button.disabled = true;
    try {
      const saved = await saveWithPrompt(link);
      if (saved === null) return;
      if (saved === false) {
        // This browser (Firefox) cannot show a Save As window to a page, so Job Finder shows the system's own.
        const names = state.links.map((item) => decodeURIComponent(item.pathname.split("/").pop()));
        status.textContent = "Choose where to save in the Save As window.";
        const result = await saveViaApp(names);
        if (result === null) {
          // The window is not available: a plain download, the browser's own settings decide where it goes.
          state.links.splice(0).forEach((item, index) => setTimeout(() => saveAsDownload(item), index * 400));
        } else {
          state.links.splice(0);
          button.textContent = state.label;
          pending.delete(button);
          status.textContent = result.saved.length ? `Saved ${result.saved.length} file${result.saved.length === 1 ? "" : "s"}.`
            : "Not saved.";
          return;
        }
      } else {
        state.links.shift();
      }
      if (state.links.length) {
        button.textContent = `Save ${state.links[0].textContent}`;
        status.textContent = `Saved. Click again to save the ${state.links[0].textContent.toLowerCase()}.`;
      } else {
        button.textContent = state.label;
        pending.delete(button);
        status.textContent = "Saved.";
      }
    } catch {
      status.textContent = "Could not save that file. Try again.";
    } finally {
      button.disabled = false;
    }
  });

  // Download Combined saves the one PDF made by Combine, the same way Download saves each file.
  section.addEventListener("click", async (event) => {
    const button = event.target.closest(".apply-queue-download-combined");
    if (!button) return;
    const link = button.closest(".apply-queue-item").querySelector(".apply-queue-combined-link");
    if (!link) {
      status.textContent = "Combine the résumé and cover letter first.";
      return;
    }
    button.disabled = true;
    try {
      const saved = await saveWithPrompt(link);
      if (saved === null) return;
      if (saved === false) {
        status.textContent = "Choose where to save in the Save As window.";
        const result = await saveViaApp([decodeURIComponent(link.pathname.split("/").pop())]);
        if (result === null) {
          saveAsDownload(link);
        } else {
          status.textContent = result.saved.length ? "Saved the combined PDF." : "Not saved.";
          return;
        }
      }
      status.textContent = "Saved the combined PDF.";
    } catch {
      status.textContent = "Could not save that file. Try again.";
    } finally {
      button.disabled = false;
    }
  });

  section.addEventListener("click", async (event) => {
    const button = event.target.closest(".apply-queue-applied, .apply-queue-remove");
    if (!button) return;
    const item = button.closest(".apply-queue-item");
    const id = item.dataset.companyId;
    button.disabled = true;
    try {
      let data = {};
      if (button.classList.contains("apply-queue-applied")) data = await send(`/apply-queue/applied/${id}`);
      else await send("/apply-queue", { company_ids: [id], queued: false });
      item.remove();
      bump("queue", -1);
      if (button.classList.contains("apply-queue-applied")) {
        // The month is the one the server saved the job under (a date typed earlier wins over today).
        const now = new Date();
        const month = /^\d{4}-\d{2}/.test(data.applied_on || "") ? data.applied_on.slice(0, 7)
          : `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
        bump("applied", 1);
        bump("total", 1);
        bump(`month-${month}`, 1);
      }
      status.textContent = button.classList.contains("apply-queue-applied") ? "Marked Applied." : "Removed from the queue.";
      refreshCount();
    } catch {
      status.textContent = "Could not save that. Try again.";
      button.disabled = false;
    }
  });
})();

// Sort the queue by Job Fit or by when a job was added. The choice is remembered in this browser; jobs added before
// the date was kept sort by the order they were saved in.
(() => {
  const select = document.getElementById("apply-queue-sort");
  const list = document.querySelector("#apply-queue .apply-queue-list");
  if (!select || !list) return;
  const KEY = "jobFinder.applyQueueSort";
  const order = (item) => Number(item.dataset.order) || 0;
  const added = (item) => item.dataset.added || "";
  const sorters = {
    oldest: (a, b) => added(a).localeCompare(added(b)) || order(a) - order(b),
    newest: (a, b) => added(b).localeCompare(added(a)) || order(b) - order(a),
    fit: (a, b) => Number(b.dataset.fit) - Number(a.dataset.fit) || order(a) - order(b),
  };
  function apply() {
    const sorter = sorters[select.value] || sorters.oldest;
    // Keep the sort control where it is on the screen: moving the rows must not scroll the page (Firefox follows a row
    // that moved), so the page is put back by however far the control shifted.
    const before = select.getBoundingClientRect().top;
    list.append(...Array.from(list.querySelectorAll(":scope > .apply-queue-item")).sort(sorter));
    const shift = select.getBoundingClientRect().top - before;
    if (shift) window.scrollBy(0, shift);
  }
  try {
    const saved = localStorage.getItem(KEY);
    if (saved && sorters[saved]) select.value = saved;
  } catch {}
  select.addEventListener("change", () => {
    try { localStorage.setItem(KEY, select.value); } catch {}
    apply();
  });
  apply();
})();
