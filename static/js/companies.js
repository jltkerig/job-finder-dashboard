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
  // The text box that used to filter results is gone: the kind tags filter them. An older cached page's box is removed.
  document.getElementById("companies-filter")?.closest("label")?.remove();
  const websiteOnly = document.getElementById("companies-website-only");
  const showHidden = document.getElementById("companies-show-hidden");
  const searchButton = document.getElementById("companies-search");
  const milesHeader = document.getElementById("companies-miles-header");
  const mineBody = document.getElementById("companies-mine-body");
  const mineCount = document.getElementById("companies-mine-count");
  const mineEmpty = document.getElementById("companies-mine-empty");
  const mineWrapper = document.getElementById("companies-mine-table-wrapper");
  const mineMilesHeader = document.getElementById("companies-mine-miles-header");
  // The results filters sit up top with the search; the kinds of place list is gone (every default kind is searched,
  // and the tags under the results narrow them). A page from the older cached template is rearranged here.
  (function arrange() {
    const kinds = form.querySelector(".companies-kinds");
    if (kinds) kinds.hidden = true; // its boxes are still read, so the usual kinds are searched
    const actions = form.querySelector(".companies-actions");
    if (!form.querySelector(".companies-filters")) {
      const bar = document.createElement("div");
      bar.className = "companies-filters";
      for (const node of [websiteOnly, showHidden]) bar.append(node.closest("label") || node);
      (kinds || actions).before(bar);
    }
    const tags = document.getElementById("companies-kind-tags");
    if (tags && !form.contains(tags)) (kinds || actions).before(tags); // an older cached page has them under the results
  })();
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const HIDDEN_KEY = "jobFinder.hiddenCompanies";
  let companies = [];
  const kindTags = document.getElementById("companies-kind-tags");
  const activeKinds = new Set();  // kinds of place picked as tags under the toolbar; none picked shows them all
  let revealHidden = false;
  let savedNames = new Set(); // names in Saved Companies, so a search result can show "Saved"
  const nameKey = (name) => String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

  const readHidden = () => {
    try { return new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) || "[]")); } catch { return new Set(); }
  };
  const writeHidden = (set) => {
    try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...set].slice(-2000))); } catch { /* remembering is a convenience */ }
    keepOnServer({ hidden: [...set].slice(-2000) });
  };
  // A company is told apart by its name and address, so it stays hidden whichever town you search from.
  const keyOf = (company) => `${company.name}|${company.address}`.toLowerCase();
  // Nearest first; a company with no map point (one you entered without a place) goes after the measured ones.
  const byDistance = (a, b) => (a.miles ?? 1e9) - (b.miles ?? 1e9) || a.name.localeCompare(b.name);

  // A copy of the last search and the hidden list is also kept by the server, so clearing the browser's data keeps them.
  function keepOnServer(part) {
    fetch("/companies/page-state", {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken }, body: JSON.stringify(part),
    }).catch(() => { /* the browser's own copy still works */ });
  }

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
  const FIELDS = ["name", "website", "jobs_board", "street", "city", "state", "zip", "phone", "notes"];

  const field = (name) => addForm.elements[name];

  // Editing a company opens the form in a row right under it; saving or cancelling closes that row and puts the form
  // back in "Add a company of your own".
  function dockForm() {
    document.querySelectorAll(".companies-edit-row").forEach((editRow) => editRow.remove());
    if (!addDetails.contains(addForm)) addDetails.insertBefore(addForm, addDetails.querySelector(".companies-add-help"));
  }

  function resetAddForm() {
    dockForm();
    addForm.reset();
    addId.value = "";
    fromSaved = "";
    addSave.textContent = "Add company";
    addCancel.hidden = true;
  }

  // A search result or a saved company can be edited too (to add its job board, say): saving puts it in My Companies,
  // and a saved one leaves Saved Companies when it does.
  let fromSaved = "";
  const ADDRESS = /^(.*?),\s*([^,]+),\s*([A-Za-z]{2})\s*(\d{5}(?:-\d{4})?)?$/;
  function editCompany(company, tr, saved) {
    dockForm();
    const isMine = Boolean(company.mine);
    const parts = isMine || company.street !== undefined ? company : (() => {
      const m = ADDRESS.exec(company.address || "");
      return m ? { street: m[1], city: m[2], state: m[3].toUpperCase(), zip: m[4] || "" } : { street: company.address || "" };
    })();
    for (const name of FIELDS) field(name).value = (name in parts ? parts[name] : company[name]) || "";
    addId.value = isMine ? company.id.replace(/^mine\//, "") : "";
    fromSaved = saved ? company.id : "";
    addSave.textContent = isMine ? "Save changes" : "Add to My Companies";
    addCancel.hidden = false;
    addStatus.textContent = "";
    const editRow = document.createElement("tr");
    editRow.className = "companies-edit-row";
    const editCell = document.createElement("td");
    editCell.colSpan = tr.children.length;
    editCell.append(addForm);
    editRow.append(editCell);
    tr.after(editRow);
    field("name").focus();
    editRow.scrollIntoView({ block: "nearest" });
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

  // Save a search result into My Companies, with what the search found. Its distance is measured from the town in the
  // box unless the address has its own ZIP code.
  async function saveCompany(company, node) {
    node.disabled = true;
    node.textContent = "Saving…";
    try {
      await post("/companies/saved", {
        name: company.name, website: company.website || "", address: company.address || "",
        phone: company.phone || "", place: /d{5}/.test(company.address || "") ? "" : place.value.trim(),
        notes: company.type ? `Found near ${place.value.trim() || "your home"}: ${company.type}` : "",
      });
      savedNames.add(nameKey(company.name));
      document.dispatchEvent(new CustomEvent("saved-companies-changed"));
      render();
    } catch (error) {
      node.disabled = false;
      node.textContent = "Save";
      say(error.message, true);
    }
  }

  // saved-companies.js (its own file) tells this page which results are already saved.
  document.addEventListener("saved-companies-loaded", (event) => {
    savedNames = new Set((event.detail?.names || []).map(nameKey));
    render();
  });

  async function removeCompany(company) {
    if (!window.confirm(`Delete ${company.name} from your companies?`)) return;
    try {
      await post(`/companies/mine/${encodeURIComponent(company.id.replace(/^mine\//, ""))}/delete`);
      await loadMine();
    } catch (error) {
      say(error.message, true);
    }
  }

  document.addEventListener("edit-saved-company", (event) => editCompany(event.detail.company, event.detail.tr, true));

  if (addForm) {
    addForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = Object.fromEntries(FIELDS.map((name) => [name, field(name).value]));
      const editing = addId.value;
      addSave.disabled = true;
      addStatus.textContent = "Saving…";
      try {
        const reply = await post(editing ? `/companies/mine/${encodeURIComponent(editing)}` : "/companies/mine", data);
        if (fromSaved) {
          await post(`/companies/saved/${encodeURIComponent(fromSaved)}/delete`).catch(() => {});
          document.dispatchEvent(new CustomEvent("saved-companies-changed"));
        }
        const synced = reply.jobs_synced || 0;
        const message = (editing ? `Saved ${reply.company.name}.` : `Added ${reply.company.name}.`)
          + (synced ? ` The job${synced === 1 ? "" : "s"} you had there ${synced === 1 ? "is" : "are"} updated in your Profile too, so the Résumé Builder shows it.` : "")
          + (reply.profile_synced === false ? " (Your Profile couldn't be updated just now.)" : "");
        resetAddForm();
        // An edit closes its row, taking the form's own message with it, so the message goes where the page shows them.
        if (editing) say(message); else addStatus.textContent = message;
        await loadMine();
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
    }
    if (company.jobs_url) {
      const jobs = document.createElement("a");
      jobs.href = company.jobs_url;
      jobs.target = "_blank";
      jobs.rel = "noopener noreferrer";
      jobs.className = "companies-jobs-link";
      jobs.title = company.jobs_url;
      jobs.textContent = company.jobs_system ? `Jobs (${company.jobs_system})` : "Jobs";
      if (company.website) site.append(document.createElement("br"));
      site.append(jobs);
    } else if (company.jobs_board) {
      // Only a name was entered ("Indeed"): shown as it was typed, not as a link.
      const jobs = document.createElement("small");
      jobs.className = "companies-jobs-text";
      jobs.textContent = `Jobs: ${company.jobs_board}`;
      if (company.website) site.append(document.createElement("br"));
      site.append(jobs);
    }
    if (!company.website && !company.jobs_url && !company.jobs_board) site.textContent = "—";
    const actions = document.createElement("td");
    actions.className = "companies-row-actions";
    if (company.mine) {
      actions.append(button("Edit", "companies-edit", () => editCompany(company, tr)),
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
      const saved = savedNames.has(nameKey(company.name));
      const save = button(saved ? "Saved" : "Save", "companies-save", () => saveCompany(company, save));
      save.disabled = saved;
      save.title = saved ? "In Saved Companies" : "Keep this company in Saved Companies";
      actions.append(save);
      actions.append(button("Edit", "companies-edit", () => editCompany(company, tr)));
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

  // ---- your own companies: their own box, listed as soon as the page opens, nearest first from the place in the box ----
  async function loadMine() {
    try {
      const response = await fetch(`/companies/mine?${new URLSearchParams({ place: place.value.trim() })}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error();
      const mine = data.companies || [];
      mineMilesHeader.textContent = data.place ? `Miles from ${data.place}` : "Miles";
      const none = new Set();
      mineBody.replaceChildren(...mine.map((company) => row(company, none)));
      mineCount.textContent = mine.length ? `(${mine.length})` : "";
      mineEmpty.hidden = mine.length > 0;
      mineWrapper.hidden = mine.length === 0;
    } catch {
      mineEmpty.hidden = false;
      mineEmpty.textContent = "Could not read your companies. Reload the page to try again.";
    }
  }

  // The kinds of place: one tag for each, always listed. After a search each shows how many were found. Picking tags
  // narrows the results already found to those kinds (none picked: all of them); it does not start a new search.
  function renderKindTags(hidden) {
    if (!kindTags) return;
    const found = new Map(); // kind -> how many
    for (const company of companies) {
      if (!company.mine && hidden.has(keyOf(company)) && !revealHidden) continue;
      for (const kind of company.kinds || []) found.set(kind, (found.get(kind) || 0) + 1);
    }
    const kinds = [...form.querySelectorAll('input[name="kinds"]')].map((box) => ({
      key: box.value, label: (box.closest("label")?.textContent || box.value).trim(),
    }));
    kindTags.hidden = false;
    kindTags.replaceChildren();
    const make = (text, active, onClick, dim) => {
      const tag = document.createElement("button");
      tag.type = "button";
      tag.className = `chip-button companies-kind-tag${active ? " is-active" : ""}${dim ? " is-empty" : ""}`;
      tag.setAttribute("aria-pressed", String(active));
      tag.textContent = text;
      tag.addEventListener("click", onClick);
      kindTags.append(tag);
    };
    make("All kinds", activeKinds.size === 0, () => { activeKinds.clear(); render(); });
    const searched = companies.length > 0;
    for (const { key, label } of kinds) {
      const count = found.get(key) || 0;
      make(searched ? `${label} (${count})` : label, activeKinds.has(key),
        () => { if (activeKinds.has(key)) activeKinds.delete(key); else activeKinds.add(key); render(); }, searched && count === 0);
    }
  }

  function render() {
    const hidden = readHidden();
    const visible = companies.filter((company) => {
      const isHidden = !company.mine && hidden.has(keyOf(company));
      if (isHidden && !revealHidden) return false;
      if (activeKinds.size && !(company.kinds || []).some((kind) => activeKinds.has(kind))) return false;
      if (websiteOnly.checked && !company.website) return false;
      return true;
    });
    body.replaceChildren(...visible.map((company) => row(company, hidden)));
    renderKindTags(hidden);
    const hiddenCount = companies.filter((company) => !company.mine && hidden.has(keyOf(company))).length;
    count.textContent = `${visible.length} of ${companies.length} companies`;
    showHidden.hidden = hiddenCount === 0;
    showHidden.textContent = revealHidden ? "Hide the hidden again" : `Show hidden (${hiddenCount})`;
    results.hidden = false;
  }

  // One kind of place at a time: OpenStreetMap's free service is slow with a big question and refuses quick repeats, so
  // each kind is its own small question. Results appear as they arrive, and one kind failing does not lose the others.
  async function search() {
    // Every kind of place is searched (the tags only narrow what comes back, so a kind that was not searched would
    // always show none). Each kind's answer is kept 6 hours, so the next search is quick.
    const allBoxes = [...form.querySelectorAll('input[name="kinds"]')];
    allBoxes.forEach((box) => { box.checked = true; });
    const boxes = allBoxes;
    if (!boxes.length) { say("Choose at least one kind of place.", true); return; }
    searchButton.disabled = true;
    companies = [];
    activeKinds.clear();
    revealHidden = false;
    const byId = new Map();
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
        measuredFrom = place.value.trim();
        milesHeader.textContent = `Miles from ${shown}`;
        const kindLabel = box.closest("label").textContent.trim();
        for (const company of data.companies || []) {
          if (seen.has(company.id)) {  // found by another kind too: it carries both tags
            const known = byId.get(company.id);
            if (known && !known.kinds.includes(box.value)) { known.kinds.push(box.value); known.kindLabels.push(kindLabel); }
            continue;
          }
          seen.add(company.id);
          company.kinds = [box.value];
          company.kindLabels = [kindLabel];
          byId.set(company.id, company);
          companies.push(company);
        }
        companies.sort(byDistance);
        render();
      } catch (error) {
        failed.push(label);
      }
    }
    searchButton.disabled = false;
    rememberResults(shown);
    loadMine();  // the miles in your own box are from this place too
    if (problem) { say(problem, true); return; }
    if (!companies.length && failed.length === boxes.length) {
      say("OpenStreetMap's free service did not answer (it is often busy). Try again in a minute.", true);
      return;
    }
    const note = failed.length ? ` Could not get: ${failed.join(", ")}. Search again to retry those.` : "";
    say(companies.length
      ? `Within ${miles.value} miles of ${shown}, nearest first. The Miles column is the distance from ${shown}.${note}`
      : `Nothing is mapped within ${miles.value} miles of ${shown || place.value} for those kinds of place. Try more kinds or a wider area.${note}`,
      failed.length > 0);
    render();
  }

  // The results are kept in this browser, so a refresh shows them again (a new search replaces them).
  const RESULTS_KEY = "jobFinder.companyResults";
  function rememberResults(shown) {
    try {
      localStorage.setItem(RESULTS_KEY, JSON.stringify({ place: place.value.trim(), shown, miles: miles.value, companies }));
    } catch { /* remembering is a convenience */ }
    keepOnServer({ results: { place: place.value.trim(), shown, miles: miles.value, companies } });
  }
  function restoreResults(fromServer) {
    try {
      const saved = fromServer || JSON.parse(localStorage.getItem(RESULTS_KEY) || "null");
      if (!saved || !Array.isArray(saved.companies) || !saved.companies.length) return;
      companies = saved.companies;
      if (saved.place) place.value = saved.place;
      if (saved.miles) miles.value = saved.miles;
      milesHeader.textContent = saved.shown ? `Miles from ${saved.shown}` : "Miles";
      say(`Your last search, around ${saved.shown || saved.place}. Press Search to look again.`);
      render();
    } catch { /* an unreadable copy is ignored */ }
  }

  // A new place in the box keeps the results already found and measures them again from it (no new search).
  let measuredFrom = place.value.trim();
  const miles_between = (a, b) => {
    const rad = Math.PI / 180;
    const h = Math.sin((b.lat - a.lat) * rad / 2) ** 2
      + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin((b.lon - a.lon) * rad / 2) ** 2;
    return 3958.8 * 2 * Math.asin(Math.sqrt(h));
  };
  place.addEventListener("change", async () => {
    const text = place.value.trim();
    if (!text || text === measuredFrom) return;
    try {
      const response = await fetch(`/companies/place?${new URLSearchParams({ place: text })}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) { say(data.message || "That place wasn't found.", true); return; }
      measuredFrom = text;
      for (const company of companies) {
        if (company.lat != null && company.lon != null) {
          company.miles = Math.round(miles_between(data.center, { lat: Number(company.lat), lon: Number(company.lon) }) * 10) / 10;
        }
      }
      companies.sort(byDistance);
      milesHeader.textContent = `Miles from ${data.place}`;
      rememberResults(data.place);
      if (companies.length) say(`Measured again from ${data.place}. Search again to look for places around there.`);
      render();
      loadMine();
      document.dispatchEvent(new CustomEvent("saved-companies-changed"));
    } catch (error) {
      say("Could not measure from that place just now.", true);
    }
  });
  restoreResults();
  measuredFrom = place.value.trim();
  // Cleared browser data: the server's copy of the last search and the hidden list come back.
  fetch("/companies/page-state", { cache: "no-store" }).then((r) => r.json()).then((state) => {
    if (Array.isArray(state.hidden) && state.hidden.length && !readHidden().size) {
      try { localStorage.setItem(HIDDEN_KEY, JSON.stringify(state.hidden)); } catch { /* ok */ }
      render();
    }
    if (!companies.length && state.results) {
      restoreResults(state.results);
      measuredFrom = place.value.trim();
    }
  }).catch(() => { /* not available: the page works without it */ });
  loadMine();
  renderKindTags(readHidden()); // every kind of place is listed from the start
  // All / None: the long list of kinds is quicker to start from one end.
  const setKinds = (on) => form.querySelectorAll('input[name="kinds"]').forEach((box) => { box.checked = on; });
  document.getElementById("companies-kinds-all")?.addEventListener("click", () => setKinds(true));
  document.getElementById("companies-kinds-none")?.addEventListener("click", () => setKinds(false));
  form.addEventListener("submit", (event) => { event.preventDefault(); search(); });
  websiteOnly.addEventListener("change", render);
  showHidden.addEventListener("click", () => { revealHidden = !revealHidden; render(); });
})();
// "My Companies" folds like the other sections (a details with a chevron). A page still served from the template cached
// before that change is rebuilt here into the same markup, and remembers its state the same way.
(function () {
  const section = document.getElementById("companies-mine");
  if (!section || section.tagName === "DETAILS") return;
  const KEY = "jobFinder.myCompaniesOpen";
  const panel = document.createElement("details");
  panel.className = "companies-mine history-collapse";
  panel.id = "companies-mine";
  const summary = document.createElement("summary");
  const heading = document.getElementById("companies-mine-heading");
  const chevron = document.createElement("span");
  chevron.className = "summary-chevron";
  chevron.setAttribute("aria-hidden", "true");
  summary.append(heading, chevron);
  panel.append(summary, ...section.childNodes);
  section.replaceWith(panel);
  try { const saved = localStorage.getItem(KEY); panel.open = saved === null ? true : saved === "1"; } catch (error) { panel.open = true; }
  panel.addEventListener("toggle", () => { try { localStorage.setItem(KEY, panel.open ? "1" : "0"); } catch (error) { /* ok */ } });
})();

// Saved Companies has its own script. It is loaded from here so it also works on a page served from a cached template.
(function () {
  const self = document.querySelector('script[src*="/static/js/companies.js"]');
  if (!self || document.querySelector('script[src*="saved-companies.js"]')) return;
  const script = document.createElement("script");
  script.src = self.src.replace("companies.js", "saved-companies.js");
  document.head.append(script);
})();
