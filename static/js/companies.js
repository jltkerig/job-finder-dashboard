// Companies page: searches OpenStreetMap (through Job Finder) for places near a town or ZIP code and lists them,
// nearest first, together with the employers Job Finder has seen and the companies you entered yourself.
// Hidden companies are remembered in this browser only; companies you entered are kept in user-data/my_companies.json.
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
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
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
  // Nearest first; a company with no map point (one you entered without a place) goes after the measured ones.
  const byDistance = (a, b) => (a.miles ?? 1e9) - (b.miles ?? 1e9) || a.name.localeCompare(b.name);

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

  function button(label, className, onClick) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = `bordered-button secondary-action ${className}`;
    node.textContent = label;
    node.addEventListener("click", onClick);
    return node;
  }

  // ---- the companies you enter yourself ----
  const addDetails = document.getElementById("companies-add");
  const addForm = document.getElementById("companies-add-form");
  const addId = document.getElementById("companies-add-id");
  const addSave = document.getElementById("companies-add-save");
  const addCancel = document.getElementById("companies-add-cancel");
  const addStatus = document.getElementById("companies-add-status");
  const FIELDS = ["name", "website", "address", "place", "phone", "notes"];

  const field = (name) => addForm.elements[name];

  function resetAddForm() {
    addForm.reset();
    addId.value = "";
    addSave.textContent = "Add company";
    addCancel.hidden = true;
  }

  function editCompany(company) {
    for (const name of FIELDS) field(name).value = company[name] || "";
    addId.value = company.id.replace(/^mine\//, "");
    addSave.textContent = "Save changes";
    addCancel.hidden = false;
    addStatus.textContent = "";
    addDetails.open = true;
    field("name").focus();
    addDetails.scrollIntoView({ block: "nearest" });
  }

  async function post(url, data) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify(data || {}),
    });
    const reply = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(reply.message || "That did not work. Try again.");
    return reply;
  }

  async function removeCompany(company) {
    if (!window.confirm(`Delete ${company.name} from your companies?`)) return;
    try {
      await post(`/companies/mine/${encodeURIComponent(company.id.replace(/^mine\//, ""))}/delete`);
      companies = companies.filter((item) => item.id !== company.id);
      render();
    } catch (error) {
      say(error.message, true);
    }
  }

  if (addForm) {
    addForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = Object.fromEntries(FIELDS.map((name) => [name, field(name).value]));
      const editing = addId.value;
      addSave.disabled = true;
      addStatus.textContent = "Saving…";
      try {
        const reply = await post(editing ? `/companies/mine/${encodeURIComponent(editing)}` : "/companies/mine", data);
        addStatus.textContent = editing ? `Saved ${reply.company.name}.` : `Added ${reply.company.name}.`;
        resetAddForm();
        // Show it at once: where it falls in the list depends on the town you are searching around, so ask again.
        const mine = form.querySelector('input[name="kinds"][value="mine"]');
        if (mine) mine.checked = true;
        if (place.value.trim()) search(); else say("Type a town or ZIP code above and press Search to see where it falls.");
      } catch (error) {
        addStatus.textContent = error.message;
      } finally {
        addSave.disabled = false;
      }
    });
    addCancel.addEventListener("click", () => { resetAddForm(); addStatus.textContent = ""; });
  }

  // ---- the list ----
  function row(company, hidden) {
    const tr = document.createElement("tr");
    if (hidden.has(keyOf(company))) tr.className = "is-hidden-company";
    tr.dataset.name = company.name.toLowerCase();
    const name = document.createElement("td");
    const strong = document.createElement("strong");
    strong.textContent = company.name;
    name.append(strong);
    if (company.mine) {
      const badge = document.createElement("span");
      badge.className = "companies-mine-badge";
      badge.textContent = "Mine";
      name.append(badge);
    }
    if (company.phone) {
      const small = document.createElement("small");
      small.className = "companies-phone";
      small.textContent = company.phone;
      name.append(document.createElement("br"), small);
    }
    if (company.notes) {
      const notes = document.createElement("small");
      notes.className = "companies-notes";
      notes.textContent = company.notes;
      name.append(notes);
    }
    const where = company.address || company.place || "—";
    tr.append(name, cell(company.type || "—"), cell(where),
      cell(company.miles === null || company.miles === undefined ? "—" : company.miles.toFixed(1), "companies-miles-cell"));
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
    if (company.mine) {
      actions.append(button("Edit", "companies-edit", () => editCompany(company)),
        button("Delete", "companies-delete", () => removeCompany(company)));
    } else {
      if (/^(node|way|relation)\//.test(company.id)) {
        const map = document.createElement("a");
        map.className = "bordered-button secondary-action";
        map.href = `https://www.openstreetmap.org/${company.id}`;
        map.target = "_blank";
        map.rel = "noopener noreferrer";
        map.textContent = "Map";
        actions.append(map);
      }
      actions.append(button(hidden.has(keyOf(company)) ? "Unhide" : "Hide", "companies-hide", () => {
        const set = readHidden();
        if (set.has(keyOf(company))) set.delete(keyOf(company)); else set.add(keyOf(company));
        writeHidden(set);
        render();
      }));
    }
    tr.append(site, actions);
    return tr;
  }

  function render() {
    const hidden = readHidden();
    const words = filter.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const visible = companies.filter((company) => {
      const isHidden = !company.mine && hidden.has(keyOf(company));
      if (isHidden && !revealHidden) return false;
      if (websiteOnly.checked && !company.website) return false;
      const haystack = `${company.name} ${company.type} ${company.address} ${company.place || ""} ${company.notes || ""}`.toLowerCase();
      return words.every((word) => haystack.includes(word));
    });
    body.replaceChildren(...visible.map((company) => row(company, hidden)));
    const hiddenCount = companies.filter((company) => !company.mine && hidden.has(keyOf(company))).length;
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
      say(`Looking for ${label} (${index + 1} of ${boxes.length})…`);
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
        companies.sort(byDistance);
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
  document.querySelectorAll(".companies-saved-place").forEach((chip) => chip.addEventListener("click", () => {
    place.value = chip.dataset.place;
    search();
  }));
})();
