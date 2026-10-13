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
      status.textContent = "Combined into one PDF. Use the Combined link to open it, or Download to save it.";
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
