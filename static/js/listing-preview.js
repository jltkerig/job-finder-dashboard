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

  // The skills Job Fit counted, as dark bubbles: ones you list show a tick, the others add themselves to your skills.
  const GROUPS = [["required", "Required skills"], ["preferred", "Nice to have"], ["other", "Also mentioned"]];

  function skillBubbles(skills) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
    const wrap = element("div", "job-posting-skills");
    const status = element("p", "field-help skill-status");
    status.setAttribute("role", "status");
    GROUPS.forEach(([where, label]) => {
      const group = skills.filter((skill) => (skill.where === where) || (where === "other" && !["required", "preferred"].includes(skill.where)));
      if (!group.length) return;
      const heading = element("h5", "", label);
      const count = () => group.filter((skill) => skill.have).length;
      const tally = element("span", "skill-tally", ` · you list ${count()} of ${group.length}`);
      heading.append(tally);
      const bubbles = element("div", "skill-bubbles");
      group.forEach((skill) => {
        const bubble = element("button", "skill-bubble" + (skill.have ? " have" : ""), skill.have ? `✓ ${skill.name}` : `+ ${skill.name}`);
        bubble.type = "button";
        bubble.disabled = skill.have;
        bubble.title = skill.have ? "Already in your skills" : "Add to your skills";
        bubble.addEventListener("click", async () => {
          bubble.disabled = true;
          try {
            const response = await fetch("/profile/add-skill", {
              method: "POST",
              headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
              body: JSON.stringify({ skill: skill.name }),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.message || "Could not add the skill.");
            skill.have = true;
            bubble.textContent = `✓ ${skill.name}`;
            bubble.classList.add("have");
            bubble.title = "Already in your skills";
            tally.textContent = ` · you list ${count()} of ${group.length}`;
            status.textContent = `Added ${result.skill || skill.name} to your skills. Job Fit updates when the page reloads.`;
          } catch (error) {
            bubble.disabled = false;
            status.textContent = error.message || "Could not add the skill.";
          }
        });
        bubbles.append(bubble);
      });
      wrap.append(heading, bubbles);
    });
    wrap.append(status);
    return wrap;
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
    if (data.skills && data.skills.length) body.append(skillBubbles(data.skills));
    if (data.text) {
      const more = element("details", "job-posting-full");
      more.append(element("summary", "", highlights.length ? "Full posting" : "Posting text"));
      more.append(element("div", "job-posting-text", data.text));
      if (!highlights.length) more.open = true;
      body.append(more);
    }
  }

  window.renderJobPosting = render; // Top 10's own Details drop-down uses the same layout

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

  // Refresh on a result row read the posting again: show the new text next time (now, if Details is open).
  document.addEventListener("listing-refreshed", (event) => {
    const id = String(event.detail?.id || "");
    loaded.delete(id);
    const section = document.querySelector(`.job-posting[data-preview-id="${id}"]`);
    const target = section?.closest("tr, [hidden], .details-row");
    if (section && target && !target.hidden) load(section);
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".details-action");
    if (!button) return;
    const target = document.getElementById(button.dataset.detailsTarget);
    const section = target?.querySelector(".job-posting");
    if (section && !target.hidden) load(section);
  });
})();
