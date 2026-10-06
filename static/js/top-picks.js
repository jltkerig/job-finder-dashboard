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
  const PICKS = 10;

  const number = (value) => { const n = parseFloat(value); return Number.isFinite(n) ? n : null; };

  // 0-100 for how close the job is: remote counts as next door; 60+ miles counts as far.
  function nearness(row) {
    if (row.dataset.workArrangement === "Remote") return 100;
    const miles = number(row.dataset.distance);
    if (miles === null || miles >= 99999) return 50;  // distance unknown
    return Math.max(0, Math.min(100, 100 - (Math.max(0, miles - 10) * 2)));
  }

  // A fit found from only one or two skills says little, so it is pulled toward the middle until five are found.
  const weigh = (score, count) => 50 + (score - 50) * Math.min(1, count / 5);

  // Signs a listing may not be a real US job: perks in the title, "anywhere in the world", low USA credibility.
  const BAIT_TITLE = /fresh grad|no experience|wfh|work from home|on[- ]job[- ]training|urgent|immediate start|\$\$|easy money|earn up to|(?:\([^)]*,[^)]*\))/i;

  function warnings(row, usa) {
    const found = [];
    if (BAIT_TITLE.test(row.dataset.job || "")) found.push({ text: "Title reads like a mass-hiring ad: check the company before applying", hard: false });
    if (/anywhere in the world|worldwide/i.test(row.dataset.state || "")) found.push({ text: "Open to anywhere in the world: may not be a US employer", hard: false });
    if (usa < 6) found.push({ text: `Low USA credibility (${Math.round(usa)}/10)`, hard: false });
    return found;
  }

  function rank(row) {
    const raw = number(row.dataset.jobFit);
    const count = number(row.dataset.fitCount) || 0;
    const fit = raw === null ? null : weigh(raw, count);
    const career = number(row.dataset.careerCredibility) || 0;  // 0-10
    const usa = number(row.dataset.usaCredibility) || 0;  // 0-10
    const near = nearness(row);
    const score = (fit === null ? 50 : fit) * 0.4 + career * 10 * 0.25 + usa * 10 * 0.15 + near * 0.2;
    const reasons = [];
    reasons.push(raw === null ? "Skill fit unknown" : `${Math.round(raw)}% skill fit · ${count} skill${count === 1 ? "" : "s"}`);
    if (row.dataset.workArrangement === "Remote") reasons.push("Remote");
    else {
      const miles = number(row.dataset.distance);
      if (miles !== null && miles < 99999) reasons.push(`${Math.round(miles)} miles away`);
    }
    reasons.push(`Credibility ${Math.round(career)}/10`);
    const gaps = warnings(row, usa);
    return { row, score: score - gaps.length * 15, reasons, gaps, fitPart: (fit === null ? 50 : fit) * 0.4 };
  }

  function shown(row) {
    return !row.hidden && row.style.display !== "none" && row.offsetParent !== null;
  }

  const byScore = (a, b) => b.score - a.score;

  function pick() {
    const rows = Array.from(document.querySelectorAll("#results-table .result-row"))
      .filter((row) => shown(row) && row.dataset.status !== "closed");
    return rows.map(rank).sort(byScore);
  }

  // Reads what the best 20 listings ask for (degrees, years, clearance) and moves down the ones the profile doesn't
  // show: a hard gap (a required degree with no alternative, an active clearance) means likely not a fit.
  async function checkRequirements(ranked) {
    const top = ranked.slice(0, PICKS * 2);
    const response = await fetch("/top-picks/requirements", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ company_ids: top.map((pick) => pick.row.dataset.companyId) }),
    });
    if (!response.ok) throw new Error("Could not read the listings' requirements.");
    const { gaps } = await response.json();
    top.forEach((pick) => {
      const found = gaps[pick.row.dataset.companyId];
      if (!found) return;
      if (found.unread) { pick.gaps.push({ text: "Couldn't read the requirements, so check them yourself", hard: false }); return; }
      pick.gaps = pick.gaps.concat(found.items);
      pick.score -= found.items.reduce((total, gap) => total + (gap.hard ? 100 : gap.avoid ? 30 : 15), 0);
      // Skills under "Requirements" count fully, "Nice to have" ones much less: replace the plain skill fit.
      if (found.fit) {
        pick.score += weigh(found.fit.score, found.fit.required) * 0.4 - pick.fitPart;
        pick.reasons[0] = found.fit.required
          ? `${found.fit.have} of ${found.fit.required} required skill${found.fit.required === 1 ? "" : "s"}`
          : `${found.fit.score}% skill fit (nice-to-haves only)`;
        if (found.fit.missing_required.length) {
          pick.gaps.push({ text: `Required skills you don't list: ${found.fit.missing_required.join(", ")}`, hard: false });
        }
      }
    });
    return top.sort(byScore).concat(ranked.slice(PICKS * 2));
  }

  function render(picks) {
    list.replaceChildren();
    picks.forEach(({ row, reasons, gaps }, index) => {
      const hard = gaps.some((gap) => gap.hard);
      const item = document.createElement("li");
      item.className = "top-pick" + (hard ? " not-a-fit" : "");
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
      const link = row.querySelector("a.inline-action-link[href^='http']");
      if (link) {
        const open = document.createElement("a");
        open.className = "top-pick-open";
        open.href = link.href;
        open.target = "_blank";
        open.rel = "noopener noreferrer";
        open.textContent = "Open listing";
        item.append(open);
      }
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
    const picks = ranked.slice(0, PICKS);
    if (!picks.length) {
      status.textContent = "No open results to pick from. Run a search or clear the filters.";
      list.replaceChildren();
    } else {
      render(picks);
      if (picks.length < PICKS) status.textContent = `Only ${picks.length} open results to pick from.`;
    }
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  document.getElementById("top-picks-close")?.addEventListener("click", () => { panel.hidden = true; });

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
