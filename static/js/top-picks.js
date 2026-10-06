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
  const { PICKS, titleWords, byScore, pick, checkRequirements, likeness, rank, appliedTitles, turnedDown } = window.TopPicksScore;

  let current = [];  // every ranked listing from the last click, best first

  // "Not for Me": reject the listing as the wrong role, learn from its title, and move the next one up.
  async function turnDown(pick, button) {
    button.disabled = true;
    try {
      const response = await fetch(`/reject-listing/${encodeURIComponent(pick.row.dataset.companyId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken, "X-Requested-With": "fetch" },
        body: JSON.stringify({ reason: "wrong_role" }),
      });
      if (!response.ok) throw new Error();
    } catch {
      button.disabled = false;
      status.textContent = "Could not reject that listing. Try again.";
      return;
    }
    const words = titleWords(pick.row.dataset.job);
    if (words.length) turnedDown.push(words);
    pick.row.hidden = true;
    const details = pick.row.nextElementSibling;
    if (details?.classList.contains("details-row")) details.hidden = true;
    current = current.filter((other) => other !== pick);
    current.forEach((other) => {
      const disliked = likeness(other.row.dataset.job, [words]);
      if (disliked >= 0.5 && !other.gaps.some((gap) => gap.text === "Like jobs you turned down")) {
        other.gaps.push({ text: "Like jobs you turned down", hard: false });
        other.score -= Math.round(35 * disliked);
      }
    });
    current.sort(byScore);
    render(current.slice(0, PICKS));
    status.textContent = "Rejected. Top 10 will mark down jobs like it from now on.";
  }

  // 👍 / 👎: saved on the listing and learned from next time; the pick stays where it is. Clicking the same one again
  // clears it.
  async function rate(pick, rating, buttons) {
    const next = pick.row.dataset.rating === rating ? "none" : rating;
    try {
      const response = await fetch("/top-picks/rate", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ company_id: pick.row.dataset.companyId, rating: next }),
      });
      if (!response.ok) throw new Error();
    } catch {
      status.textContent = "Could not save the rating. Try again.";
      return;
    }
    const words = titleWords(pick.row.dataset.job);
    const drop = (list) => { const at = list.findIndex((item) => item.join(" ") === words.join(" ")); if (at >= 0) list.splice(at, 1); };
    if (pick.row.dataset.rating === "up") drop(appliedTitles);
    if (pick.row.dataset.rating === "down") drop(turnedDown);
    if (next === "up") appliedTitles.push(words);
    if (next === "down") turnedDown.push(words);
    pick.row.dataset.rating = next === "none" ? "" : next;
    buttons.up.classList.toggle("is-on", next === "up");
    buttons.down.classList.toggle("is-on", next === "down");
    buttons.up.setAttribute("aria-pressed", String(next === "up"));
    buttons.down.setAttribute("aria-pressed", String(next === "down"));
    status.textContent = next === "none" ? "Rating cleared." : "Thanks. Top 10 will use that next time.";
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

  function savedPicks() {
    try {
      const saved = JSON.parse(localStorage.getItem(SAVED_KEY) || "null");
      if (!saved || Date.now() - saved.at > SAVED_FOR_MS) return [];
      return saved.picks.map((pick) => {
        const row = document.querySelector(`#results-table .result-row[data-company-id="${pick.id}"]`);
        return row && row.dataset.status !== "closed" ? { row, score: pick.score, reasons: pick.reasons || [], gaps: pick.gaps || [] } : null;
      }).filter(Boolean);
    } catch {
      return [];
    }
  }

  function render(picks) {
    savePicks(picks);
    list.replaceChildren();
    picks.forEach((pick, index) => {
      const { row, reasons, gaps } = pick;
      const hard = gaps.some((gap) => gap.hard);
      const item = document.createElement("li");
      item.className = "top-pick" + (hard ? " not-a-fit" : "");
      item.dataset.score = Math.round(pick.score);
      const rankNumber = document.createElement("span");
      rankNumber.className = "top-pick-rank";
      rankNumber.textContent = index + 1;
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
      const company = document.createElement("span");
      company.className = "top-pick-company";
      company.textContent = row.dataset.company || "";
      const chips = document.createElement("div");
      chips.className = "top-pick-chips";
      reasons.forEach((reason) => {
        const chip = document.createElement("span");
        chip.className = "top-pick-chip";
        chip.textContent = reason;
        chips.append(chip);
      });
      body.append(title, company, chips);
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
      item.append(rankNumber, box, body);
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
        keep.textContent = rowKeep.classList.contains("is-saved") ? "Saved" : "Keep";
        keep.addEventListener("click", () => {
          rowKeep.click();
          setTimeout(() => {
            keep.textContent = rowKeep.textContent.trim();
            keep.classList.toggle("is-saved", rowKeep.classList.contains("is-saved"));
          }, 800);
        });
        buttons.append(keep);
      }
      // Details drops down under the pick: the posting's requirements, duties and text, read the first time.
      const details = document.createElement("button");
      details.type = "button";
      details.className = "bordered-button top-pick-details-button";
      details.textContent = "Details";
      details.setAttribute("aria-expanded", "false");
      const drop = document.createElement("div");
      drop.className = "top-pick-details";
      drop.hidden = true;
      details.addEventListener("click", async () => {
        drop.hidden = !drop.hidden;
        details.setAttribute("aria-expanded", String(!drop.hidden));
        details.textContent = drop.hidden ? "Details" : "Hide Details";
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
      buttons.append(details);
      const reject = document.createElement("button");
      reject.type = "button";
      reject.className = "bordered-button destructive-action";
      reject.textContent = "Reject";
      reject.title = "Reject this listing; Top 10 marks down jobs like it from now on.";
      reject.addEventListener("click", () => turnDown(pick, reject));
      buttons.append(reject);
      const queueButton = document.createElement("button");
      queueButton.type = "button";
      queueButton.className = "bordered-button";
      queueButton.textContent = row.dataset.queued ? "Added to Apply" : "Apply";
      queueButton.disabled = Boolean(row.dataset.queued);
      queueButton.title = "Add to the Apply queue on your Dashboard";
      queueButton.addEventListener("click", async () => {
        queueButton.disabled = true;
        try {
          await queue([row.dataset.companyId]);
          queueButton.textContent = "Added to Apply";
          row.dataset.queued = "1";
        } catch (error) {
          status.textContent = error.message;
          queueButton.disabled = false;
        }
      });
      buttons.append(queueButton);
      const ratings = document.createElement("div");
      ratings.className = "top-pick-rating";
      const up = document.createElement("button");
      const down = document.createElement("button");
      [[up, "up", "👍", "Good pick"], [down, "down", "👎", "Bad pick"]].forEach(([button, value, icon, label]) => {
        button.type = "button";
        button.className = "top-pick-thumb" + (row.dataset.rating === value ? " is-on" : "");
        button.textContent = icon;
        button.title = label;
        button.setAttribute("aria-label", label);
        button.setAttribute("aria-pressed", String(row.dataset.rating === value));
        button.addEventListener("click", () => rate(pick, value, { up, down }));
        ratings.append(button);
      });
      actions.append(buttons, ratings);
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
    if (restored.length) {
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
