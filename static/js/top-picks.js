// Top 10 picks: ranks the results shown on the Search page and lists the 10 best to apply to. After you untick any
// you don't want, "Build" keeps them on the Dashboard and copies one request for Résumé Builder's Claude Desktop
// connector to make a tailored résumé and cover letter for each.
(function () {
  const button = document.getElementById("top-picks-button");
  const panel = document.getElementById("top-picks");
  if (!button || !panel) return;
  const list = document.getElementById("top-picks-list");
  const status = document.getElementById("top-picks-status");
  const build = document.getElementById("top-picks-build");
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  if (!window.TopPicksScore) return;
  const { isHandled, PICKS, titleWords, byScore, pick, checkRequirements, likeness, rank, appliedTitles } = window.TopPicksScore;

  let current = [];  // every ranked listing from the last click, best first

  // A pick's buttons read "Word icon": the icon sits to the right of the word, and follows it when the word changes.
  const ICONS = { Keep: "⭐", Saved: "✔️", Apply: "📨", Added: "✔️", Details: "🔍", "Hide Details": "🔼", Refresh: "🔄",
    "Refreshing…": "⏳", Reject: "🚫", Closed: "🔒", "Block Company": "🚷", "Block Domain": "🌐" };
  function setLabel(target, text) {
    const label = Object.assign(document.createElement("span"), { className: "btn-label", textContent: text });
    const icon = Object.assign(document.createElement("span"), { className: "btn-icon", textContent: ICONS[text] || "" });
    icon.setAttribute("aria-hidden", "true");
    target.replaceChildren(label, icon);
  }

  // The green / red / grey dot of a job's Open / Closed / Unknown status, as on the Search page's rows.
  function statusDot(row) {
    const state = ["open", "closed"].includes(row.dataset.status) ? row.dataset.status : "unknown";
    const dot = document.createElement("span");
    dot.className = `status-dot ${state}`;
    dot.dataset.tooltip = state === "open" ? "This listing appears to still be active."
      : state === "closed" ? "This listing appears to no longer be active." : "The current listing status could not be confirmed.";
    return dot;
  }

  // Reject / Closed: take the listing off the Search page and move the next pick up. Neither teaches Top 10 anything.
  async function rejectListing(pick, button, reason, message) {
    button.disabled = true;
    try {
      const response = await fetch(`/reject-listing/${encodeURIComponent(pick.row.dataset.companyId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken, "X-Requested-With": "fetch" },
        body: JSON.stringify({ reason }),
      });
      if (!response.ok) throw new Error();
    } catch {
      button.disabled = false;
      status.textContent = "Could not reject that listing. Try again.";
      return;
    }
    window.requestReplacementSearch?.();  // a Top 10 pick is replaced by a new search result
    pick.row.hidden = true;
    const details = pick.row.nextElementSibling;
    if (details?.classList.contains("details-row")) details.hidden = true;
    current = current.filter((other) => other !== pick);
    render(current.slice(0, PICKS));
    status.textContent = message;
  }

  // Block Company / Block Domain: the same call as the Search page's block buttons. The employer's jobs leave the results
  // and the Top 10, and the next picks move up.
  async function blockEmployer(pick, button, kind, question) {
    if (!confirm(question)) return;
    const id = pick.row.dataset.companyId;
    button.disabled = true;
    try {
      const response = await fetch(`/block-${kind}/${encodeURIComponent(id)}`, {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken, "X-Requested-With": "fetch", Accept: "application/json" },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.message || "Could not block that.");
      const company = (pick.row.dataset.company || "").toLowerCase();
      const domainOf = (row) => (row.querySelector('td[data-label="Domain"] a')?.textContent || "").trim().toLowerCase();
      const domain = domainOf(pick.row);
      const same = (row) => (kind === "company" ? (row.dataset.company || "").toLowerCase() === company
        : Boolean(domain) && domainOf(row) === domain);
      document.querySelectorAll("#results-table .result-row").forEach((row) => {
        if (!same(row)) return;
        const details = row.nextElementSibling;
        if (details?.classList.contains("details-row")) details.remove();
        row.remove();
      });
      current = current.filter((other) => document.contains(other.row));
      window.requestReplacementSearch?.();
      if (current.length < PICKS) {  // some picks went with it: rank again so the list is full
        document.getElementById("top-picks-button")?.click();
        return;
      }
      render(current.slice(0, PICKS));
      status.textContent = `${kind === "company" ? (data.company || pick.row.dataset.company || "That company") : (data.domain || domain)} is blocked from future searches. The next picks moved up.`;
    } catch (error) {
      button.disabled = false;
      status.textContent = error.message || "Could not block that.";
    }
  }

  // Keep or Apply on a pick: it is handled, so it leaves the list and the next best one moves up.
  function dropPick(pick, message) {
    current = current.filter((other) => other !== pick);
    if (current.length < PICKS) {  // a remembered list holds only 10: rank again so the list is full
      button.click();
      return;
    }
    render(current.slice(0, PICKS));
    status.textContent = message;
  }

  // Apply queue: queued jobs are kept on the Dashboard, where Apply opens them. After a daily search, Top 10 runs by
  // itself and queues the strong picks: no hard gaps and a score of at least AUTO_QUEUE_SCORE.
  const AUTO_QUEUE_SCORE = 65;
  let autoQueue = panel.dataset.autoQueue === "1";

  async function queue(ids, extra = {}) {
    const response = await fetch("/apply-queue", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ company_ids: ids, queued: true, ...extra }),
    });
    if (!response.ok) throw new Error("Could not add to the Apply queue.");
  }

  async function queueStrongPicks(picks) {
    const strong = picks.filter((pick) => !pick.gaps.some((gap) => gap.hard) && pick.score >= AUTO_QUEUE_SCORE);
    try {
      await queue(strong.map((pick) => pick.row.dataset.companyId), { auto: true });
      status.textContent = strong.length
        ? `Daily search done: queued ${strong.length} strong ${strong.length === 1 ? "pick" : "picks"} on your Dashboard to apply to.`
        : "Daily search done: no strong picks to queue today.";
    } catch (error) {
      status.textContent = error.message;
    }
  }

  // The last Top 10 is remembered in this browser for a day, so a page refresh shows it again without re-ranking.
  const SAVED_KEY = "jobFinderTopPicks";
  const SAVED_FOR_MS = 24 * 60 * 60 * 1000;

  function savePicks(picks) {
    try {
      localStorage.setItem(SAVED_KEY, JSON.stringify({ at: Date.now(), picks: picks.map((pick) => ({
        id: pick.row.dataset.companyId, score: pick.score, reasons: pick.reasons, gaps: pick.gaps })) }));
    } catch {}
  }

  function savedCount() {
    try { return (JSON.parse(localStorage.getItem(SAVED_KEY) || "null")?.picks || []).length; } catch { return 0; }
  }

  function savedPicks() {
    try {
      const saved = JSON.parse(localStorage.getItem(SAVED_KEY) || "null");
      if (!saved || Date.now() - saved.at > SAVED_FOR_MS) return [];
      return saved.picks.map((pick) => {
        const row = document.querySelector(`#results-table .result-row[data-company-id="${pick.id}"]`);
        return row && row.dataset.status !== "closed" && !isHandled(row) ? { row, score: pick.score, reasons: pick.reasons || [], gaps: pick.gaps || [] } : null;
      }).filter(Boolean);
    } catch {
      return [];
    }
  }

  function render(picks) {
    savePicks(picks);
    list.replaceChildren();
    picks.forEach((pick) => {
      const { row, reasons, gaps } = pick;
      const hard = gaps.some((gap) => gap.hard);
      const item = document.createElement("li");
      item.className = "top-pick" + (hard ? " not-a-fit" : "");
      item.dataset.score = Math.round(pick.score);
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = !hard;
      box.value = row.dataset.companyId;
      box.dataset.job = row.dataset.job || "";
      box.dataset.company = row.dataset.company || "";
      box.id = `top-pick-${row.dataset.companyId}`;
      box.setAttribute("aria-label", `Include ${row.dataset.job || "this job"}`);
      const body = document.createElement("div");
      body.className = "top-pick-body";
      const title = document.createElement("label");
      title.className = "top-pick-title";
      title.htmlFor = box.id;
      title.textContent = row.dataset.job || `Job #${row.dataset.companyId}`;
      title.append(" ", statusDot(row));
      const chips = document.createElement("div");
      chips.className = "top-pick-chips";
      reasons.forEach((reason) => {
        const chip = document.createElement("span");
        chip.className = "top-pick-chip";
        chip.textContent = reason;
        chips.append(chip);
      });
      body.append(title);
      if (window.TopPicksFacts) {
        body.append(window.TopPicksFacts.line(row, row.dataset.company || ""));
        const domain = window.TopPicksFacts.domain(row);
        if (domain) body.append(domain);
        body.append(window.TopPicksFacts.stats(row));
      } else {
        body.append(Object.assign(document.createElement("span"), { className: "top-pick-company", textContent: row.dataset.company || "" }));
      }
      body.append(chips);
      if (gaps.length) {
        const warnings = document.createElement("ul");
        warnings.className = "top-pick-gaps";
        gaps.forEach((gap) => {
          const warning = document.createElement("li");
          warning.className = gap.hard ? "hard" : "";
          warning.textContent = (gap.hard ? "Likely not a fit: " : "") + gap.text;
          warnings.append(warning);
        });
        body.append(warnings);
      }
      item.append(box, body);  // the order of the list is the ranking
      const actions = document.createElement("div");
      actions.className = "top-pick-actions";
      const link = row.querySelector("a.inline-action-link[href^='http']");
      if (link) {
        const open = document.createElement("a");
        open.className = "top-pick-open";
        open.href = link.href;
        open.target = "_blank";
        open.rel = "noopener noreferrer";
        open.textContent = "Open listing";
        actions.append(open);
      }
      const buttons = document.createElement("div");
      buttons.className = "top-pick-buttons";
      // Keep works like the row's own Keep button (saves the job to your Dashboard).
      const rowKeep = row.querySelector(".keep-action");
      if (rowKeep) {
        const keep = document.createElement("button");
        keep.type = "button";
        keep.className = "bordered-button keep-action-pick" + (rowKeep.classList.contains("is-saved") ? " is-saved" : "");
        setLabel(keep, rowKeep.classList.contains("is-saved") ? "Saved" : "Keep");
        keep.addEventListener("click", () => {
          rowKeep.click();
          setTimeout(() => {
            setLabel(keep, rowKeep.textContent.trim());
            keep.classList.toggle("is-saved", rowKeep.classList.contains("is-saved"));
            if (isHandled(row)) dropPick(pick, "Saved to your Dashboard. The next pick moved up.");
          }, 800);
        });
        buttons.append(keep);
      }
      // Details drops down under the pick: the posting's requirements, duties and text, read the first time.
      const details = document.createElement("button");
      details.type = "button";
      details.className = "bordered-button top-pick-details-button";
      setLabel(details, "Details");
      details.setAttribute("aria-expanded", "false");
      const drop = document.createElement("div");
      drop.className = "top-pick-details";
      drop.hidden = true;
      details.addEventListener("click", async () => {
        drop.hidden = !drop.hidden;
        details.setAttribute("aria-expanded", String(!drop.hidden));
        setLabel(details, drop.hidden ? "Details" : "Hide Details");
        if (drop.hidden || drop.dataset.loaded) return;
        drop.dataset.loaded = "1";
        drop.replaceChildren(Object.assign(document.createElement("p"), { className: "field-help job-posting-loading", textContent: "Reading the posting…" }));
        try {
          const response = await fetch(`/listing-preview/${encodeURIComponent(row.dataset.companyId)}`, { cache: "no-store" });
          const data = await response.json();
          if (!response.ok) throw new Error(data.message || "Could not read the posting.");
          window.renderJobPosting(drop, data);
        } catch (error) {
          delete drop.dataset.loaded;
          drop.replaceChildren(Object.assign(document.createElement("p"), { className: "field-help", textContent: error.message || "Could not read the posting." }));
        }
        if (link) {
          drop.append(Object.assign(document.createElement("a"), { className: "top-pick-open top-pick-details-open", href: link.href,
            target: "_blank", rel: "noopener noreferrer", textContent: "Open listing" }));
        }
      });
      const reject = document.createElement("button");
      reject.type = "button";
      reject.className = "bordered-button destructive-action";
      setLabel(reject, "Reject");
      reject.title = "Reject this listing and take it off the Search page.";
      reject.addEventListener("click", async () => {
        // "If rejecting, why?": the same reasons as the picker on a result's Details tab.
        const reason = window.askRejectReason ? await window.askRejectReason(row.dataset.job) : "other";
        if (reason) rejectListing(pick, reject, reason, "Rejected.");
      });
      const closed = document.createElement("button");
      closed.type = "button";
      closed.className = "bordered-button destructive-action";
      setLabel(closed, "Closed");
      closed.title = "The job is closed. Rejects it without teaching Top 10 anything.";
      closed.addEventListener("click", () => rejectListing(pick, closed, "closed", "Rejected as closed."));
      // Refresh: reads this one posting again and re-checks its requirements (it may have closed or changed).
      const refreshOne = document.createElement("button");
      refreshOne.type = "button";
      refreshOne.className = "bordered-button";
      setLabel(refreshOne, "Refresh");
      refreshOne.title = "Read this posting again and re-check its requirements";
      refreshOne.addEventListener("click", async () => {
        refreshOne.disabled = true;
        setLabel(refreshOne, "Refreshing…");
        try {
          const [fresh] = await checkRequirements([rank(pick.row)], { refresh: true });
          if (!fresh) {  // it has closed since: the server marked it Closed
            dropPick(pick, "That posting has closed, so it was removed. The next pick moved up.");
            return;
          }
          current = current.map((other) => (other === pick ? fresh : other)).sort(byScore);
          render(current.slice(0, PICKS));
          status.textContent = "Refreshed: the posting was read again.";
          // Say so on the pick itself too: the status line is at the bottom of the panel, out of sight.
          const shown = document.getElementById(`top-pick-${fresh.row.dataset.companyId}`)?.closest(".top-pick");
          if (shown) {
            shown.querySelector(".top-pick-body")?.append(Object.assign(document.createElement("p"),
              { className: "top-pick-refreshed", textContent: "✓ Refreshed just now: the posting was read again." }));
          }
        } catch (error) {
          status.textContent = error.message || "Could not refresh that listing. Try again.";
          refreshOne.disabled = false;
          setLabel(refreshOne, "Refresh");
        }
      });
      const queueButton = document.createElement("button");
      queueButton.type = "button";
      queueButton.className = "bordered-button";
      setLabel(queueButton, row.dataset.queued ? "Added" : "Apply");
      queueButton.disabled = Boolean(row.dataset.queued);
      queueButton.title = "Add to the Apply queue on your Dashboard";
      queueButton.addEventListener("click", async () => {
        queueButton.disabled = true;
        try {
          await queue([row.dataset.companyId]);
          setLabel(queueButton, "Added");
          row.dataset.queued = "1";
          const rowApply = row.querySelector(".apply-action");
          if (rowApply) { rowApply.textContent = "Added"; rowApply.disabled = true; }
          window.removeResultRows?.([row.dataset.companyId]);  // an added job leaves the results table too
          dropPick(pick, "Added to your Apply queue. The next pick moved up.");
        } catch (error) {
          status.textContent = error.message;
          queueButton.disabled = false;
        }
      });
      const keepButton = buttons.querySelector(".keep-action-pick");
      if (keepButton) keepButton.after(queueButton); else buttons.prepend(queueButton);  // Keep and Apply together
      // Two columns: Keep | Apply, Details | Refresh, Reject | Closed.
      buttons.replaceChildren(...[buttons.querySelector(".keep-action-pick"), queueButton, details, refreshOne, reject, closed]
        .filter(Boolean));
      // Block: keeps this employer out of future searches. A job site's own address (linkedin.com) is never blocked.
      const blocks = [["Block Company", "company", `Block ${row.dataset.company || "this company"} from future searches?`]];
      const domainLink = row.querySelector('td[data-label="Domain"] a');
      if (!row.dataset.jobSiteDomain && domainLink) {
        blocks.push(["Block Domain", "domain", `Block ${domainLink.textContent.trim()} from future searches? This blocks the entire website, not just this listing.`]);
      }
      blocks.forEach(([label, kind, question]) => {
        const block = document.createElement("button");
        block.type = "button";
        block.className = "bordered-button destructive-action top-pick-block";
        setLabel(block, label);
        block.title = kind === "company" ? "Exclude this company name from future searches." : "Hide this website and all future jobs from it.";
        block.addEventListener("click", () => blockEmployer(pick, block, kind, question));
        buttons.append(block);
      });
      actions.append(buttons);
      item.append(actions, drop);
      list.append(item);
    });
  }

  button.addEventListener("click", async () => {
    let ranked = pick();
    status.textContent = "";
    panel.hidden = false;
    if (ranked.length) {
      list.replaceChildren();
      status.textContent = "Reading each listing's requirements…";
      status.classList.add("loading");
      button.disabled = true;
      try {
        ranked = await checkRequirements(ranked);
        status.textContent = "";
      } catch (error) {
        status.textContent = `${error.message} Ranked by skills, distance and credibility only.`;
      }
      status.classList.remove("loading");
      button.disabled = false;
    }
    current = ranked;
    const picks = ranked.slice(0, PICKS);
    if (!picks.length) {
      status.textContent = "No open results to pick from. Run a search or clear the filters.";
      list.replaceChildren();
    } else {
      render(picks);
      if (picks.length < PICKS) status.textContent = `Only ${picks.length} open results to pick from.`;
    }
    if (autoQueue) {
      autoQueue = false;
      await queueStrongPicks(picks);
    }
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  });


  if (autoQueue) {
    button.click();
  } else {
    const restored = savedPicks();
    // Some saved picks have since closed, been rejected or left the results: rank again rather than show a short list.
    if (restored.length && restored.length < savedCount()) {
      button.click();
    } else if (restored.length) {
      current = restored;
      render(restored);
      panel.hidden = false;
      status.textContent = "Your last Top 10. Click Top 10 Picks to rank again.";
    }
  }

  document.getElementById("top-picks-close")?.addEventListener("click", () => {
    panel.hidden = true;
    try { localStorage.removeItem(SAVED_KEY); } catch {}  // closed: don't bring it back on the next refresh
  });

  build.addEventListener("click", async () => {
    const chosen = Array.from(list.querySelectorAll("input:checked"));
    if (!chosen.length) { status.textContent = "Tick at least one job first."; return; }
    build.disabled = true;
    status.textContent = "Keeping them on your Dashboard…";
    try {
      const response = await fetch("/save-kept", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ company_ids: chosen.map((box) => box.value) }),
      });
      if (!response.ok) throw new Error("Could not keep these jobs on the Dashboard.");
    } catch (error) {
      status.textContent = error.message;
      build.disabled = false;
      return;
    }
    const jobs = chosen.map((box) => {
      const job = [box.dataset.job, box.dataset.company].filter(Boolean).join(" at ");
      return `- Job Finder job #${box.value}${job ? ` (${job})` : ""}`;
    }).join("\n");
    const request = "Using Résumé Builder, read my writing rules, then for each of these Job Finder jobs tailor my " +
      "résumé and write a cover letter. Read the whole listing first each time, and save both as PDFs.\n" + jobs;
    try {
      await navigator.clipboard.writeText(request);
      status.textContent = `Kept ${chosen.length} on the Dashboard. Request copied: paste it in Claude Desktop.`;
    } catch {
      window.prompt("Copy this request, then paste it into Claude Desktop:", request);
      status.textContent = `Kept ${chosen.length} on the Dashboard.`;
    }
    build.disabled = false;
    window.location.href = "claude://";
  });
})();
