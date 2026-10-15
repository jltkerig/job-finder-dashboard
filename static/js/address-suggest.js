// Companies page: the Address box suggests addresses as you type (from /companies/address-suggest, which asks OpenStreetMap
// through Photon). Picking one fills the street, city, state and ZIP boxes, and the company name when it is
// empty and the suggestion is a business. Arrow keys, Enter and Escape work; nothing is filled until one is chosen.
(() => {
  const input = document.getElementById("companies-add-street");
  const cityBox = document.getElementById("companies-add-city");
  const stateBox = document.getElementById("companies-add-state");
  const zipBox = document.getElementById("companies-add-zip");
  const nameBox = document.getElementById("companies-add-name");
  if (!input) return;

  const list = document.createElement("ul");
  list.id = "companies-add-street-list";
  list.className = "address-suggest";
  list.setAttribute("role", "listbox");
  list.hidden = true;
  input.parentElement.classList.add("address-suggest-wrap");
  input.after(list);
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", list.id);
  input.setAttribute("aria-expanded", "false");

  let timer = null, asked = "", active = -1, shown = [], request = null;

  function close() {
    list.hidden = true;
    list.replaceChildren();
    shown = [];
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function mark(index) {
    active = index;
    [...list.children].forEach((item, position) => {
      item.classList.toggle("is-active", position === index);
      item.setAttribute("aria-selected", String(position === index));
    });
    if (index >= 0) input.setAttribute("aria-activedescendant", list.children[index].id);
    else input.removeAttribute("aria-activedescendant");
  }

  function choose(suggestion) {
    input.value = suggestion.street || suggestion.address;
    asked = input.value.trim(); // so the filled-in street is not asked about again
    if (cityBox) cityBox.value = suggestion.city || "";
    if (stateBox) stateBox.value = suggestion.state || "";
    if (zipBox) zipBox.value = suggestion.zip || "";
    if (nameBox && !nameBox.value.trim() && suggestion.name) nameBox.value = suggestion.name;
    close();
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function show(suggestions) {
    close();
    if (!suggestions.length) return;
    shown = suggestions;
    suggestions.forEach((suggestion, index) => {
      const item = document.createElement("li");
      item.id = `${list.id}-${index}`;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", "false");
      const line = document.createElement("span");
      line.textContent = suggestion.address;
      item.append(line);
      if (suggestion.detail) {
        const detail = document.createElement("small");
        detail.textContent = suggestion.detail;
        item.append(detail);
      }
      item.addEventListener("mousedown", (event) => { event.preventDefault(); choose(suggestion); }); // before the box loses focus
      item.addEventListener("mousemove", () => mark(index));
      list.append(item);
    });
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  async function ask() {
    const typed = input.value.trim();
    if (typed.length < 4 || typed === asked) return;
    asked = typed;
    if (request) request.abort();
    request = new AbortController();
    try {
      const town = [cityBox && cityBox.value.trim(), stateBox && stateBox.value.trim()].filter(Boolean).join(", ") || (zipBox ? zipBox.value.trim() : "");
      const params = new URLSearchParams({ q: typed, place: town });
      const response = await fetch(`/companies/address-suggest?${params}`, { cache: "no-store", signal: request.signal });
      const data = await response.json();
      if (input.value.trim() !== typed || document.activeElement !== input) return; // typed on, or left the box
      show(response.ok && Array.isArray(data.suggestions) ? data.suggestions : []);
    } catch (error) {
      if (error.name !== "AbortError") close(); // no suggestions is the same as a service that is not there
    }
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    if (input.value.trim().length < 4) { asked = ""; close(); return; }
    timer = setTimeout(ask, 400);
  });
  input.addEventListener("keydown", (event) => {
    if (list.hidden || !shown.length) return;
    if (event.key === "ArrowDown") { event.preventDefault(); mark((active + 1) % shown.length); }
    else if (event.key === "ArrowUp") { event.preventDefault(); mark((active - 1 + shown.length) % shown.length); }
    else if (event.key === "Enter" && active >= 0) { event.preventDefault(); choose(shown[active]); }
    else if (event.key === "Escape") { event.preventDefault(); close(); }
  });
  input.addEventListener("blur", () => { clearTimeout(timer); close(); });
})();
