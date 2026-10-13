// Top 10 picks: the same facts a listing row shows (pay, work location, distance, credibility, job fit, domain, found
// date), read from the pick's row in the results table so a pick never needs a trip back to the table.
(function () {
  const cellText = (row, label) => {
    const cell = row.querySelector(`td[data-label="${label}"]`);
    return cell ? cell.textContent.replace(/\s+/g, " ").trim() : "";
  };

  // [{label, value, title?}] in the order they are shown; blank values are left out.
  function facts(row) {
    const meta = row.querySelector('td[data-label="Job"] .job-meta');
    const location = cellText(row, "Location");
    const distance = cellText(row, "Distance");
    const domain = row.querySelector('td[data-label="Domain"] a');
    const verification = row.querySelector('td[data-label="Domain"] .verification');
    const fit = row.dataset.jobFit;
    const found = row.querySelector('td[data-label="Found"]');
    const items = [
      { label: "Pay", value: meta ? meta.textContent.replace(/\s+/g, " ").trim() : "" },
      { label: "Work", value: cellText(row, "Work Location").replace(/^—$/, "") },
      { label: "Location", value: location + (distance && distance !== "—" ? ` (${distance})` : "") },
      { label: "Job fit", value: fit ? `${fit}%` : "" },
      { label: "Career credibility", value: cellText(row, "Career Credibility") },
      { label: "USA credibility", value: cellText(row, "USA Credibility") },
      { label: "Domain", value: domain ? domain.textContent.trim() : "" },
      { label: "Found", value: found?.dataset.found ? (found.title.split(". Last checked")[0] || "").replace(/^Found /, "") : "" },
    ];
    if (verification) items.push({ label: "Source", value: verification.textContent.trim() });
    return items.filter((item) => item.value);
  }

  function build(row) {
    const list = document.createElement("dl");
    list.className = "top-pick-facts";
    facts(row).forEach(({ label, value, title }) => {
      const group = document.createElement("div");
      if (title) group.title = title;
      group.append(Object.assign(document.createElement("dt"), { textContent: label }),
        Object.assign(document.createElement("dd"), { textContent: value }));
      list.append(group);
    });
    return list;
  }

  window.TopPicksFacts = { build };
})();
