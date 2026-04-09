async function bootCatalog() {
  const root = document.querySelector("[data-source-catalog]");
  if (!root) {
    return;
  }

  const input = document.querySelector("[data-catalog-input]");
  const tierButtons = Array.from(document.querySelectorAll("[data-filter-tier]"));
  const countNode = document.querySelector("[data-catalog-count]");
  const grid = document.querySelector("[data-catalog-grid]");

  let activeTier = "all";
  let inventory = [];

  try {
    const response = await fetch(root.dataset.sourceCatalog);
    inventory = await response.json();
  } catch (error) {
    grid.innerHTML = "<p>Could not load the source inventory.</p>";
    return;
  }

  function render() {
    const term = (input.value || "").trim().toLowerCase();
    const filtered = inventory.filter((entry) => {
      const tierMatch = activeTier === "all" || entry.tier === activeTier;
      if (!tierMatch) {
        return false;
      }

      if (!term) {
        return true;
      }

      const haystack = [
        entry.name,
        entry.scope,
        entry.authority,
        entry.notes,
        entry.use_case,
        entry.category
      ]
        .join(" ")
        .toLowerCase();

      return haystack.includes(term);
    });

    countNode.textContent = String(filtered.length);

    if (!filtered.length) {
      grid.innerHTML = "<p>No sources matched this filter.</p>";
      return;
    }

    grid.innerHTML = filtered
      .map((entry) => {
        const searchModes = Array.isArray(entry.search_modes) ? entry.search_modes.join(", ") : "";
        return `
          <article class="source-card">
            <div class="source-meta">
              <span>${entry.tier}</span>
              <span>${entry.priority}</span>
              <span>${entry.category}</span>
            </div>
            <h3><a href="${entry.url}" target="_blank" rel="noreferrer">${entry.name}</a></h3>
            <p>${entry.scope}</p>
            <p><strong>Authority:</strong> ${entry.authority}</p>
            <p><strong>Format:</strong> ${entry.format}</p>
            <p><strong>Use:</strong> ${entry.use_case}</p>
            <p><strong>Notes:</strong> ${entry.notes}</p>
            ${searchModes ? `<p><strong>Search modes:</strong> ${searchModes}</p>` : ""}
          </article>
        `;
      })
      .join("");
  }

  input.addEventListener("input", render);

  tierButtons.forEach((button) => {
    button.addEventListener("click", () => {
      activeTier = button.dataset.filterTier;
      tierButtons.forEach((candidate) => {
        candidate.classList.toggle("is-active", candidate === button);
      });
      render();
    });
  });

  render();
}

bootCatalog();
