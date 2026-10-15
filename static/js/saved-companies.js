// Companies page: "Saved Companies", the search results you kept with Save, below My Companies. It folds like the other
// sections and remembers that in this browser. Companies.js tells this file when a result was saved.
(function () {
  const anchor = document.getElementById("companies-mine");
  const place = document.getElementById("companies-place");
  if (!anchor || !place) return;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const KEY = "jobFinder.savedCompaniesOpen";

  const panel = document.createElement("details");
  panel.className = "companies-mine companies-saved history-collapse";
  panel.id = "companies-saved";
  panel.innerHTML = '<summary><h2 id="companies-saved-heading">Saved Companies <span class="field-help" id="companies-saved-count"></span></h2>'
    + '<span class="summary-chevron" aria-hidden="true"></span></summary>'
    + '<p class="field-help" id="companies-saved-empty">None yet. Press Save on a company in the search results to keep it here.</p>'
    + '<div class="table-wrapper companies-table-wrapper" id="companies-saved-wrapper" hidden><table class="companies-table">'
    + '<thead><tr><th>Company</th><th>Address</th><th id="companies-saved-miles">Miles</th><th>Website</th><th><span class="visually-hidden">Actions</span></th></tr></thead>'
    + '<tbody id="companies-saved-body"></tbody></table></div>';
  anchor.after(panel);
  try { const saved = localStorage.getItem(KEY); panel.open = saved === null ? true : saved === "1"; } catch (error) { panel.open = true; }
  panel.addEventListener("toggle", () => { try { localStorage.setItem(KEY, panel.open ? "1" : "0"); } catch (error) { /* ok */ } });

  const body = panel.querySelector("#companies-saved-body");
  const wrapper = panel.querySelector("#companies-saved-wrapper");
  const empty = panel.querySelector("#companies-saved-empty");
  const count = panel.querySelector("#companies-saved-count");
  const miles = panel.querySelector("#companies-saved-miles");

  function cell(text) {
    const td = document.createElement("td");
    td.textContent = text || "—";
    return td;
  }

  async function remove(company) {
    if (!window.confirm(`Remove ${company.name} from your saved companies?`)) return;
    try {
      const response = await fetch(`/companies/saved/${encodeURIComponent(company.id)}/delete`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: "{}",
      });
      if (!response.ok) throw new Error();
      load();
    } catch (error) {
      empty.hidden = false;
      empty.textContent = "Couldn't remove it. Reload the page and try again.";
    }
  }

  function row(company) {
    const tr = document.createElement("tr");
    const name = cell(company.name);
    name.dataset.label = "Company";
    const site = document.createElement("td");
    if (company.website) {
      const link = document.createElement("a");
      link.href = company.website;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = company.website.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
      site.append(link);
    } else {
      site.textContent = "—";
    }
    const actions = document.createElement("td");
    actions.className = "companies-row-actions";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "bordered-button secondary-action companies-delete";
    button.textContent = "Remove";
    button.addEventListener("click", () => remove(company));
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "bordered-button secondary-action companies-edit";
    edit.textContent = "Edit";
    edit.title = "Add its job board, notes or a fixed address. It moves to My Companies.";
    edit.addEventListener("click", () => document.dispatchEvent(new CustomEvent("edit-saved-company", { detail: { company, tr } })));
    actions.append(edit, button);
    tr.className = "companies-clickable";
    tr.title = "Click to see everything about this company";
    tr.addEventListener("click", (event) => {
      if (event.target.closest("a, button")) return;
      document.dispatchEvent(new CustomEvent("show-company", { detail: { company, tr } }));
    });
    tr.append(name, cell(company.address), cell(company.miles == null ? "" : String(company.miles)), site, actions);
    return tr;
  }

  async function load() {
    try {
      const response = await fetch(`/companies/saved?${new URLSearchParams({ place: place.value.trim() })}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error();
      const saved = data.companies || [];
      miles.textContent = data.place ? `Miles from ${data.place}` : "Miles";
      body.replaceChildren(...saved.map(row));
      count.textContent = saved.length ? `(${saved.length})` : "";
      empty.hidden = saved.length > 0;
      empty.textContent = "None yet. Press Save on a company in the search results to keep it here.";
      wrapper.hidden = saved.length === 0;
      document.dispatchEvent(new CustomEvent("saved-companies-loaded", { detail: { names: saved.map((company) => company.name) } }));
    } catch (error) {
      empty.hidden = false;
      empty.textContent = "Could not read your saved companies. Reload the page to try again.";
    }
  }

  document.addEventListener("saved-companies-changed", load);
  document.getElementById("companies-form")?.addEventListener("submit", () => setTimeout(load, 1500)); // new place: new distances
  load();
})();
