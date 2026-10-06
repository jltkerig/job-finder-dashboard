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

  function rank(row) {
    const raw = number(row.dataset.jobFit);
    const count = number(row.dataset.fitCount) || 0;
    const fit = raw === null ? null : weigh(raw, count);
    const career = number(row.dataset.careerCredibility) || 0;  // 0-10
    const usa = number(row.dataset.usaCredibility) || 0;  // 0-10
    const near = nearness(row);
    const score = (fit === null ? 50 : fit) * 0.4 + career * 10 * 0.25 + usa * 10 * 0.15 + near * 0.2;
    const reasons = [];
    reasons.push(raw === null ? "skill fit unknown" : `${Math.round(raw)}% skill fit (${count} skill${count === 1 ? "" : "s"} listed)`);
    if (row.dataset.workArrangement === "Remote") reasons.push("remote");
    else {
      const miles = number(row.dataset.distance);
      if (miles !== null && miles < 99999) reasons.push(`${Math.round(miles)} miles away`);
    }
    reasons.push(`credibility ${Math.round(career)}/10`);
    return { row, score, reasons, gaps: [], fitPart: (fit === null ? 50 : fit) * 0.4 };
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
      if (found.unread) { pick.gaps = [{ text: "Couldn't read the requirements: check them yourself", hard: false }]; return; }
      pick.gaps = found.items.slice();
      pick.score -= found.items.reduce((total, gap) => total + (gap.hard ? 100 : 15), 0);
      // Skills under "Requirements" count fully, "Nice to have" ones much less: replace the plain skill fit.
      if (found.fit) {
        pick.score += weigh(found.fit.score, found.fit.required) * 0.4 - pick.fitPart;
        pick.reasons[0] = `${found.fit.score}% skill fit, weighted by required skills`;
        if (found.fit.missing_required.length) {
          pick.gaps.push({ text: `Required skills you don't list: ${found.fit.missing_required.join(", ")}`, hard: false });
        }
      }
    });
    return top.sort(byScore).concat(ranked.slice(PICKS * 2));
  }

  function render(picks) {
    list.replaceChildren();
    picks.forEach(({ row, reasons, gaps }) => {
      const item = document.createElement("li");
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = true;
      box.value = row.dataset.companyId;
      box.dataset.job = row.dataset.job || "";
      box.dataset.company = row.dataset.company || "";
      const title = document.createElement("strong");
      title.textContent = [row.dataset.job, row.dataset.company].filter(Boolean).join(" at ") || `Job #${row.dataset.companyId}`;
      const why = document.createElement("span");
      why.className = "top-picks-why";
      why.textContent = reasons.join(" · ");
      label.append(box, " ", title, why);
      gaps.forEach((gap) => {
        const warning = document.createElement("span");
        warning.className = "top-picks-gap" + (gap.hard ? " hard" : "");
        warning.textContent = (gap.hard ? "Likely not a fit: " : "Check: ") + gap.text;
        label.append(warning);
      });
      if (gaps.some((gap) => gap.hard)) box.checked = false;
      item.append(label);
      const link = row.querySelector("a.inline-action-link[href^='http']");
      if (link) {
        const open = document.createElement("a");
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
      button.disabled = true;
      try {
        ranked = await checkRequirements(ranked);
        status.textContent = "";
      } catch (error) {
        status.textContent = `${error.message} Ranked by skills, distance and credibility only.`;
      }
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
