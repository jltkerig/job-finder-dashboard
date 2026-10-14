// Top 10 picks: the same facts a listing card shows (company and place, domain, Job Fit / credibility / found pills), read
// from the pick's row in the results table so a pick never needs a trip back to the table, and laid out like that card.
(function () {
  const cellText = (row, label) => {
    const cell = row.querySelector(`td[data-label="${label}"]`);
    return cell ? cell.textContent.replace(/\s+/g, " ").trim() : "";
  };

  function span(className, text) {
    const node = document.createElement("span");
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // "Mach7 Technologies · United States (Remote) · Remote · Up to $70K/yr": the company, then the place and the rest.
  function line(row, company) {
    const wrap = span("top-pick-line");
    const parts = [];
    const place = cellText(row, "Location");
    const distance = cellText(row, "Distance");
    if (place) parts.push(place + (distance && distance !== "—" ? ` (${distance})` : ""));
    const work = cellText(row, "Work Location").replace(/^—$/, "");
    if (work) parts.push(work);
    const pay = row.querySelector('td[data-label="Job"] .job-pay');
    if (pay) parts.push(pay.textContent.replace(/\s+/g, " ").trim());
    if (company) wrap.append(span("top-pick-company", company));
    parts.forEach((text) => wrap.append(span("top-pick-place", text)));
    return wrap;
  }

  // "linkedin.com  No company website found; not verified"
  function domain(row) {
    const link = row.querySelector('td[data-label="Domain"] a');
    const verification = row.querySelector('td[data-label="Domain"] .verification');
    if (!link && !verification) return null;
    const wrap = span("top-pick-domain");
    if (link) {
      const anchor = document.createElement("a");
      anchor.href = link.href;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.textContent = link.textContent.trim();
      wrap.append(anchor);
    }
    if (verification) wrap.append(span("top-pick-verification", verification.textContent.trim()));
    return wrap;
  }

  function pill(label, value) {
    const wrap = span("top-pick-stat");
    wrap.append(span("stat-label", label), value);
    return wrap;
  }

  // The Job Fit / Career / USA / Found pills, with the row's own badges (same colors) inside.
  function stats(row) {
    const wrap = span("top-pick-stats");
    const fit = row.querySelector('td[data-label="Job Fit"] .job-fit');
    if (fit) wrap.append(pill("Job Fit", fit.cloneNode(true)));
    [["Career", "Career Credibility"], ["USA", "USA Credibility"]].forEach(([label, cell]) => {
      const badge = row.querySelector(`td[data-label="${cell}"] .credibility-badge`);
      if (badge) wrap.append(pill(label, badge.cloneNode(true)));
    });
    const found = row.querySelector('td[data-label="Found"]');
    if (found?.dataset.found) {
      const date = (found.firstChild?.textContent || "").trim();
      const age = found.querySelector(".found-age")?.textContent.replace(/\s+/g, " ").replace(/^on results\s*/i, "").trim();
      if (date) wrap.append(pill("Found", span("stat-plain", age ? `${date} · ${age}` : date)));
    }
    return wrap;
  }

  window.TopPicksFacts = { line, domain, stats };
})();
