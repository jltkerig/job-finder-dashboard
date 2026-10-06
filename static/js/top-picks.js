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

  // How closely the listing's title matches one of your job titles, 0-100. Words are compared by their first five
  // letters ("design" ~ "designer"); listing words that none of your titles use ("highway", "telecommunications")
  // count against it, while level and work-type words ("senior", "remote", "II") don't.
  const GENERIC_TITLE_WORDS = new Set(["senior", "sr", "junior", "jr", "staff", "lead", "principal", "mid", "level", "entry",
    "i", "ii", "iii", "iv", "remote", "hybrid", "onsite", "site", "contract", "full", "time", "part", "temporary", "the",
    "and", "of", "for", "with", "to", "in", "at", "a", "an", "us", "usa"]);
  // Job boards dress titles up: "Charter Global is hiring: Graphic Designer in Baltimore" is a Graphic Designer.
  const cleanTitle = (text) => (text || "").replace(/^.*?\bis hiring:?\s*/i, "")
    .replace(/\s+in\s+[A-Z][a-zA-Z]+(?:[\s,]+[A-Z][a-zA-Z]*)*\s*$/, "").replace(/\s+[-–|]\s+[^-–|]+$/, (tail) =>
      (/designer|developer|producer|specialist|manager|artist/i.test(tail) ? tail : ""));
  const titleWords = (text) => cleanTitle(text).toLowerCase()
    .replace(/front[\s-]+end/g, "frontend").replace(/back[\s-]+end/g, "backend").replace(/\([^)]*\)|\[[^\]]*\]/g, " ")
    .split(/[^a-z0-9]+/).filter((word) => word && !GENERIC_TITLE_WORDS.has(word) && !/^\d+$/.test(word))
    .map((word) => (word === "webmaster" ? "web" : word).slice(0, 5));
  let profileTitles = [];
  try { profileTitles = JSON.parse(panel.dataset.titles || "[]").map(titleWords).filter((words) => words.length); } catch {}
  // Jobs you applied to show what you really go for: their titles' words count as yours, and listings like them
  // move up.
  let appliedTitles = [];
  try {
    appliedTitles = [...JSON.parse(panel.dataset.applied || "[]"), ...JSON.parse(panel.dataset.liked || "[]")]
      .map(titleWords).filter((words) => words.length);
  } catch {}
  // Words close to most design and web titles count as yours even when none of your titles use them.
  const knownWords = new Set([...profileTitles.flat(), ...appliedTitles.flat(), "ux", "ui", "produ", "inter", "creat", "multi", "websi"]);

  // Listings you turned down with "Not for Me" (or rejected as the wrong role) teach the other way.
  let turnedDown = [];
  try { turnedDown = JSON.parse(panel.dataset.turnedDown || "[]").map(titleWords).filter((words) => words.length); } catch {}

  // 0-1: how much the title shares with the closest of the examples (shared words over all words of both).
  function likeness(title, examples) {
    const words = new Set(titleWords(title));
    if (!examples.length || !words.size) return 0;
    return Math.max(...examples.map((applied) => {
      const shared = applied.filter((word) => words.has(word)).length;
      return shared / new Set([...applied, ...words]).size;
    }));
  }

  function titleMatch(title) {
    const words = titleWords(title);
    if (!profileTitles.length || !words.length) return null;
    const known = words.filter((word) => knownWords.has(word)).length / words.length;
    const best = Math.max(...profileTitles.map((mine) => mine.filter((word) => words.includes(word)).length / mine.length));
    return Math.round(100 * best * known);
  }

  const CLEARANCE_TITLE = /ts\s*\/\s*sci|top secret|polygraph|\bpoly\b|security clearance|\bclearance\b|\bsecret\b/i;

  function warnings(row, usa) {
    const found = [];
    if (CLEARANCE_TITLE.test(row.dataset.job || "")) found.push({ text: "The title asks for a security clearance", hard: true });
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
    // Skill fit counts most; credibility is how much the source is trusted (every LinkedIn listing gets 3/10), not how
    // well the job fits, so it counts less.
    const score = (fit === null ? 50 : fit) * 0.5 + career * 10 * 0.15 + usa * 10 * 0.1 + near * 0.25;
    const reasons = [];
    reasons.push(raw === null ? "Skill fit unknown" : `${Math.round(raw)}% skill fit · ${count} skill${count === 1 ? "" : "s"}`);
    if (row.dataset.workArrangement === "Remote") reasons.push("Remote");
    else {
      const miles = number(row.dataset.distance);
      if (miles !== null && miles < 99999) reasons.push(`${Math.round(miles)} miles away`);
    }
    reasons.push(`Credibility ${Math.round(career)}/10`);
    const gaps = warnings(row, usa);
    let penalty = gaps.reduce((total, gap) => total + (gap.hard ? 100 : 15), 0);
    const match = titleMatch(row.dataset.job);
    if (match !== null && match < 40) {
      gaps.push({ text: "The title doesn't match your job titles", hard: false });
      penalty += 40;
    } else if (match !== null && match < 80) {
      reasons.push("Title partly matches");
      penalty += 10;
    } else if (match !== null) {
      reasons.push("Title matches");
    }
    const liked = likeness(row.dataset.job, appliedTitles);
    const disliked = likeness(row.dataset.job, turnedDown);
    if (liked >= 0.5) reasons.push("Like jobs you applied to");
    if (disliked >= 0.5) gaps.push({ text: "Like jobs you turned down", hard: false });
    return { row, score: score - penalty + Math.round(20 * liked) - Math.round(35 * disliked), reasons, gaps, fitPart: (fit === null ? 50 : fit) * 0.5 };
  }

  function shown(row) {
    return !row.hidden && row.style.display !== "none" && row.offsetParent !== null;
  }

  const byScore = (a, b) => b.score - a.score;
  // Pages that aren't one job (same idea as looks_like_not_a_job on the server): directories, job lists, agency pages.
  const NOT_A_JOB = /^\s*(?:find|hire|compare|top\s+\d+|best)\s|\bjobs\b|\b(?:web|website|graphic|logo|wordpress)\s+design\s+(?:in|near|services?|company|agency|packages?)\b/i;

  function pick() {
    const rows = Array.from(document.querySelectorAll("#results-table .result-row"))
      .filter((row) => shown(row) && row.dataset.status !== "closed" && !NOT_A_JOB.test(row.dataset.job || "")
        && !["Applied", "Talking With Recruiter", "Interview"].includes(row.dataset.application));
    return rows.map(rank).sort(byScore);
  }

  // Reads what the best 40 listings ask for (degrees, years, clearance) and moves down the ones the profile doesn't
  // show: a hard gap (a required degree with no alternative, an active clearance) means likely not a fit.
  async function checkRequirements(ranked) {
    const top = ranked.slice(0, PICKS * 4);
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
      pick.score -= found.items.reduce((total, gap) => total + (gap.hard ? 100 : gap.weight || (gap.avoid ? 30 : 15)), 0);
      // Skills under "Requirements" count fully, "Nice to have" ones much less: replace the plain skill fit.
      if (found.fit) {
        pick.score += weigh(found.fit.score, found.fit.required || found.fit.listed) * 0.5 - pick.fitPart;
        pick.reasons[0] = found.fit.required
          ? `${found.fit.have} of ${found.fit.required} required skill${found.fit.required === 1 ? "" : "s"}`
          : `${found.fit.have_listed} of ${found.fit.listed} listed skills (none required)`;
        if (found.fit.missing_required.length) {
          pick.gaps.push({ text: `Required skills you don't list: ${found.fit.missing_required.join(", ")}`, hard: false });
        }
      }
    });
    return top.sort(byScore).concat(ranked.slice(PICKS * 4));
  }

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

  function render(picks) {
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
      queueButton.textContent = "Queue";
      queueButton.title = "Add to the Apply queue on your Dashboard";
      queueButton.addEventListener("click", async () => {
        queueButton.disabled = true;
        try {
          await queue([row.dataset.companyId]);
          queueButton.textContent = "Queued";
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

  if (autoQueue) button.click();

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
