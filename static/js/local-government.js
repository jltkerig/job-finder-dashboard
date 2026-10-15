// Search page: after you type a place (a town and state, or a ZIP) in the city box, suggests the local governments around
// it that post their own jobs (county, town, state), and lets you watch one with a click. Nothing is added until you click.
(function () {
  const input = document.getElementById("search-city-input");
  const box = document.getElementById("local-gov-suggestions");
  if (!input || !box) return;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  let timer = 0, asked = "";

  function show(text, items) {
    box.replaceChildren();
    box.hidden = !text;
    if (!text) return;
    const heading = document.createElement("p");
    heading.className = "field-help local-gov-heading";
    heading.textContent = text;
    box.append(heading);
    for (const item of items || []) {
      const row = document.createElement("div");
      row.className = "local-gov-item";
      const label = document.createElement("span");
      const link = document.createElement("a");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = item.name;
      label.append(link, ` · ${item.kind.toLowerCase()} government`);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "bordered-button";
      button.textContent = item.watched ? "Watching" : "Watch their jobs";
      button.disabled = Boolean(item.watched);
      button.addEventListener("click", async () => {
        button.disabled = true;
        button.textContent = "Adding…";
        try {
          const response = await fetch("/local-governments/watch", {
            method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
            body: JSON.stringify({ agency: item.agency, name: item.name }),
          });
          if (!response.ok) throw new Error();
          button.textContent = "Watching";
        } catch (error) {
          button.disabled = false;
          button.textContent = "Couldn't add. Try again";
        }
      });
      row.append(label, button);
      box.append(row);
    }
  }

  async function look() {
    const place = input.value.trim();
    if (place.length < 3 || place === asked) return;
    asked = place;
    show("Looking for local governments…", []);
    try {
      const response = await fetch(`/local-governments?place=${encodeURIComponent(place)}`);
      const data = await response.json().catch(() => ({}));
      if (place !== input.value.trim()) return; // typed on since
      if (!response.ok) { show(""); return; }
      const where = [data.area.town, data.area.county_name].filter(Boolean).join(" · ");
      show(data.found.length ? `Local governments near ${place} (${where}) that post jobs:`
        : `No local government job pages found for ${place} (${where}).`, data.found);
    } catch (error) {
      show("");
    }
  }

  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(look, 900); });
  input.addEventListener("change", look);
})();
