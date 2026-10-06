// The Job Posting section of a listing's Details: loads some of the posting the first time the details open, so you
// can skim what it asks for and reject it from the Search page without opening the site.
(function () {
  const loaded = new Set();

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function render(body, data) {
    body.replaceChildren();
    if (data.unread) {
      body.append(element("p", "field-help", "Couldn't read this posting here. Open the listing to see it."));
      return;
    }
    if (data.gaps && data.gaps.length) {
      const warnings = element("ul", "top-pick-gaps");
      data.gaps.forEach((gap) => {
        warnings.append(element("li", gap.hard ? "hard" : "", (gap.hard ? "Likely not a fit: " : "") + gap.text));
      });
      body.append(warnings);
    }
    const highlights = Object.entries(data.highlights || {});
    if (highlights.length) {
      const grid = element("div", "job-posting-highlights");
      highlights.forEach(([name, lines]) => {
        const block = element("div");
        block.append(element("h5", "", name));
        const list = element("ul");
        lines.forEach((line) => list.append(element("li", "", line)));
        block.append(list);
        grid.append(block);
      });
      body.append(grid);
    }
    if (data.text) {
      const more = element("details", "job-posting-full");
      more.append(element("summary", "", highlights.length ? "Full posting" : "Posting text"));
      more.append(element("div", "job-posting-text", data.text));
      if (!highlights.length) more.open = true;
      body.append(more);
    }
  }

  async function load(section) {
    const id = section.dataset.previewId;
    if (!id || loaded.has(id)) return;
    loaded.add(id);
    const body = section.querySelector(".job-posting-body");
    body.replaceChildren(element("p", "field-help job-posting-loading", "Reading the posting…"));
    try {
      const response = await fetch(`/listing-preview/${encodeURIComponent(id)}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || "Could not read the posting.");
      render(body, data);
    } catch (error) {
      loaded.delete(id);
      body.replaceChildren(element("p", "field-help", error.message || "Could not read the posting."));
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".details-action");
    if (!button) return;
    const target = document.getElementById(button.dataset.detailsTarget);
    const section = target?.querySelector(".job-posting");
    if (section && !target.hidden) load(section);
  });
})();
