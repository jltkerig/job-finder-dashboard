// Asks before importing jobs saved by the Web Job Scraper Firefox extension.
// Nothing is moved or imported until "Yes" is clicked; "Not now" hides the prompt until new files arrive.
(() => {
  "use strict";
  const box = document.getElementById("capture-prompt");
  if (!box) return;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const DISMISSED_KEY = "jobFinderCapturePromptDismissed";

  function dismissedSignature() {
    try { return localStorage.getItem(DISMISSED_KEY) || ""; } catch { return ""; }
  }

  function rememberDismissed(signature) {
    try { localStorage.setItem(DISMISSED_KEY, signature); } catch { /* the prompt just comes back next time */ }
  }

  function button(label, className, onClick) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = `bordered-button ${className}`;
    node.textContent = label;
    node.addEventListener("click", onClick);
    return node;
  }

  function showMessage(text) {
    const message = document.createElement("p");
    message.textContent = text;
    box.replaceChildren(message);
    box.hidden = false;
  }

  async function importNow(signature, buttons) {
    buttons.forEach((node) => { node.disabled = true; });
    try {
      const response = await fetch("/captures/import", { method: "POST", headers: { "X-CSRF-Token": csrfToken } });
      const data = await response.json();
      if (!response.ok) {
        const message = data.message || "The import could not be started.";
        throw new Error(data.error_code ? `${message} [${data.error_code}]` : message);
      }
      if (data.status === "already_running") {
        showMessage("Job Finder is busy with another task. The prompt will come back when it finishes.");
        return;
      }
      rememberDismissed(signature);
      showMessage(`Moved ${data.moved} file${data.moved === 1 ? "" : "s"}. Importing…`);
      // charts.js notices the running import within a couple of seconds, shows its progress and reloads when done.
    } catch (error) {
      showMessage(error.message || "The import could not be started.");
      buttons.forEach((node) => { node.disabled = false; });
    }
  }

  function render(data) {
    const files = `${data.count} new capture file${data.count === 1 ? "" : "s"}`;
    const jobs = `${data.jobs} job${data.jobs === 1 ? "" : "s"}`;
    const heading = document.createElement("strong");
    heading.textContent = `Found ${files} (${jobs}) in ${data.from}.`;
    const question = document.createElement("p");
    question.textContent = `Move them to ${data.to} and import them?`;
    const actions = document.createElement("div");
    actions.className = "capture-prompt-actions";
    const yes = button("Yes", "primary-action", () => importNow(data.signature, [yes, notNow]));
    const notNow = button("Not now", "secondary-action", () => {
      rememberDismissed(data.signature);
      box.hidden = true;
    });
    actions.append(yes, notNow);
    box.replaceChildren(heading, question, actions);
    box.hidden = false;
  }

  async function check() {
    if (!box.hidden && box.querySelector("button:disabled")) return; // an import is being started
    try {
      const response = await fetch("/captures/pending", { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      if (!data.count || data.signature === dismissedSignature()) {
        if (box.querySelector("button")) box.hidden = true;
        return;
      }
      render(data);
    } catch {
      // The dashboard is restarting; try again on the next check.
    }
  }

  check();
  setInterval(check, 60000);
})();
