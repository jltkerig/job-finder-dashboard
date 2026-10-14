// Companies page: searches OpenStreetMap (through Job Finder) for places near a town or ZIP code and lists them,
// nearest first. Hidden companies are remembered in this browser only.
(function () {
  const form = document.getElementById("companies-form");
  if (!form) return;
  const place = document.getElementById("companies-place");
  const miles = document.getElementById("companies-miles");
  const status = document.getElementById("companies-status");
  const results = document.getElementById("companies-results");
  const body = document.getElementById("companies-body");
  const count = document.getElementById("companies-count");
  const filter = document.getElementById("companies-filter");
  const websiteOnly = document.getElementById("companies-website-only");
  const showHidden = document.getElementById("companies-show-hidden");
  const searchButton = document.getElementById("companies-search");
  const HIDDEN_KEY = "jobFinder.hiddenCompanies";
  let companies = [];
  let revealHidden = false;

  const readHidden = () => {
    try { return new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) || "[]")); } catch { return new Set(); }
  };
  const writeHidden = (set) => {
    try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...set].slice(-2000))); } catch { /* remembering is a convenience */ }
  };
  // A company is told apart by its name and address, so it stays hidden whichever town you search from.
  const keyOf = (company) => `${company.name}|${company.address}`.toLowerCase();

  function say(message, isError) {
    status.textContent = message || "";
    status.classList.toggle("companies-error", Boolean(isError));
  }

  function cell(text, className) {
    const td = document.createElement("td");
    if (className) td.className = className;
    td.textContent = text;
    return td;
  }

  function row(company, hidden) {
    const tr = document.createElement("tr");
    if (hidden.has(keyOf(company))) tr.className = "is-hidden-company";
    tr.dataset.name = company.name.toLowerCase();
    const name = document.createElement("td");
    const strong = document.createElement("strong");
    strong.textContent = company.name;
    name.append(strong);
    if (company.phone) {
      const small = document.createElement("small");
      small.className = "companies-phone";
      small.textContent = company.phone;
      name.append(document.createElement("br"), small);
    }
    tr.append(name, cell(company.type || "—"), cell(company.address || "—"), cell(company.miles.toFixed(1), "companies-miles-cell"));
    const site = document.createElement("td");
    if (company.website) {
      const link = document.createElement("a");
      link.href = company.website;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = company.website.replace(/^https?:\/\/(www\.)?/i, "").replace(/\/$/, "");
      site.append(link);
    } else {
      site.textContent = "—";
    }
    const actions = document.createElement("td");
    actions.className = "companies-row-actions";
    const map = document.createElement("a");
    map.className = "bordered-button secondary-action";
    map.href = `https://www.openstreetmap.org/${company.id}`;
    map.target = "_blank";
    map.rel = "noopener noreferrer";
    map.textContent = "Map";
    const hide = document.createElement("button");
    hide.type = "button";
    hide.className = "bordered-button secondary-action companies-hide";
    hide.textContent = hidden.has(keyOf(company)) ? "Unhide" : "Hide";
    hide.addEventListener("click", () => {
      const set = readHidden();
      if (set.has(keyOf(company))) set.delete(keyOf(company)); else set.add(keyOf(company));
      writeHidden(set);
      render();
    });
    actions.append(map, hide);
    tr.append(site, actions);
    return tr;
  }

  function render() {
    const hidden = readHidden();
    const words = filter.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const visible = companies.filter((company) => {
      const isHidden = hidden.has(keyOf(company));
      if (isHidden && !revealHidden) return false;
      if (websiteOnly.checked && !company.website) return false;
      const haystack = `${company.name} ${company.type} ${company.address}`.toLowerCase();
      return words.every((word) => haystack.includes(word));
    });
    body.replaceChildren(...visible.map((company) => row(company, hidden)));
    const hiddenCount = companies.filter((company) => hidden.has(keyOf(company))).length;
    count.textContent = `${visible.length} of ${companies.length} companies`;
    showHidden.hidden = hiddenCount === 0;
    showHidden.textContent = revealHidden ? "Hide the hidden again" : `Show hidden (${hiddenCount})`;
    results.hidden = false;
  }

  // One kind of place at a time: OpenStreetMap's free service is slow with a big question and refuses quick repeats, so
  // each kind is its own small question. Results appear as they arrive, and one kind failing does not lose the others.
  async function search() {
    const boxes = [...form.querySelectorAll('input[name="kinds"]:checked')];
    if (!boxes.length) { say("Choose at least one kind of place.", true); return; }
    searchButton.disabled = true;
    companies = [];
    revealHidden = false;
    const seen = new Set();
    const failed = [];
    let shown = "";
    let problem = "";
    for (const [index, box] of boxes.entries()) {
      const label = box.closest("label").textContent.trim().toLowerCase();
      say(`Looking for ${label} (${index + 1} of ${boxes.length})… the first can take a little while.`);
      const params = new URLSearchParams({ place: place.value.trim(), miles: miles.value, kinds: box.value });
      try {
        const response = await fetch(`/companies/search?${params}`, { cache: "no-store" });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          if (response.status < 500) { problem = data.message || "The search did not work."; break; } // the place itself is wrong
          throw new Error(data.message || "The search did not work.");
        }
        shown = data.place;
        for (const company of data.companies || []) {
          if (seen.has(company.id)) continue;
          seen.add(company.id);
          companies.push(company);
        }
        companies.sort((a, b) => a.miles - b.miles || a.name.localeCompare(b.name));
        render();
      } catch (error) {
        failed.push(label);
      }
    }
    searchButton.disabled = false;
    if (problem) { say(problem, true); return; }
    if (!companies.length && failed.length === boxes.length) {
      say("OpenStreetMap's free service did not answer (it is often busy). Try again in a minute.", true);
      return;
    }
    const note = failed.length ? ` Could not get: ${failed.join(", ")}. Search again to retry those.` : "";
    say(companies.length
      ? `Near ${shown}, within ${miles.value} miles. Nearest first.${note}`
      : `Nothing is mapped within ${miles.value} miles of ${shown || place.value} for those kinds of place. Try more kinds or a wider area.${note}`,
      failed.length > 0);
    render();
  }

  form.addEventListener("submit", (event) => { event.preventDefault(); search(); });
  filter.addEventListener("input", render);
  websiteOnly.addEventListener("change", render);
  showHidden.addEventListener("click", () => { revealHidden = !revealHidden; render(); });
  document.querySelectorAll(".companies-saved-place").forEach((button) => button.addEventListener("click", () => {
    place.value = button.dataset.place;
    search();
  }));
})();
