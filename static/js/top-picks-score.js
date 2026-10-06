// Top 10 scoring: ranks the listings on the Search page by skill fit, distance, credibility and title match,
// marked up or down by what you applied to, liked and turned down. top-picks.js draws and handles the picks.
(function () {
  const panel = document.getElementById("top-picks");
  if (!panel) return;
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


  // appliedTitles and turnedDown are shared: top-picks.js adds to them when you rate or reject a pick.
  window.TopPicksScore = { PICKS, titleWords, byScore, pick, checkRequirements, likeness, rank, appliedTitles, turnedDown };
})();
