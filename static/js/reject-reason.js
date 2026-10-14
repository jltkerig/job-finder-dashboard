// "If rejecting, why?" for a Reject button that has no Details tab with the picker beside it (a Top 10 pick, a saved job
// on the Dashboard). The same reasons as the picker on a result's Details tab. window.askRejectReason(name) resolves to
// the reason, or null when you cancel.
(() => {
  const REASONS = [["other", "Other reason"], ["wrong_role", "Wrong role"], ["wrong_location", "Wrong location"],
    ["not_a_job", "Not a job"], ["duplicate", "Duplicate"], ["closed", "Job is closed"]];
  let dialog, title, select;

  function button(text, value, classes) {
    const element = document.createElement("button");
    element.type = "submit";
    element.value = value;
    element.className = `bordered-button ${classes}`;
    element.textContent = text;
    return element;
  }

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "review-dialog reject-reason-dialog";
    const form = document.createElement("form");
    form.method = "dialog";
    title = document.createElement("h2");
    const label = document.createElement("label");
    label.className = "reject-reason-label";
    select = document.createElement("select");
    select.className = "reject-reason";
    REASONS.forEach(([value, text]) => select.append(new Option(text, value)));
    label.append("If rejecting, why? ", select);
    const actions = document.createElement("div");
    actions.className = "review-actions";
    actions.append(button("Cancel", "cancel", "secondary-action"), button("Reject", "reject", "primary-action"));
    form.append(title, label, actions);
    dialog.append(form);
    document.body.append(dialog);
  }

  window.askRejectReason = (name) => new Promise((resolve) => {
    if (typeof HTMLDialogElement === "undefined") { resolve("other"); return; }
    if (!dialog) build();
    if (dialog.open) { resolve(null); return; }  // one question at a time
    title.textContent = `Reject ${name || "this listing"}?`;
    select.value = "other";
    dialog.returnValue = "";
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "reject" ? select.value : null), { once: true });
    dialog.showModal();
    select.focus();
  });
})();
