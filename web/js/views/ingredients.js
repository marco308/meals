// The ingredient catalogue: aisles, staples and the household's own
// premium/budget verdicts (Q17 — never guessed, so this is where they get
// set). Also the duplicates clean-up (Q21), which finally gets a screen —
// until now only an AI over MCP could run it.

import { aisles, api } from "../api.js";
import { confirmDialog, emptyState, html, openDialog, render, skeleton, toast } from "../dom.js";

let query = { search: "", staplesOnly: false, tier: "" };

export async function renderIngredients(root) {
  render(root, skeleton());
  const [items, aisleList] = await Promise.all([
    api("/ingredients", {
      query: {
        search: query.search || undefined,
        staples_only: query.staplesOnly || undefined,
        value_tier: query.tier || undefined,
      },
    }),
    aisles(),
  ]);

  render(root, html`
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Ingredients</h1>
          <p class="sub">aisles, staples, and your own ⭐ premium / 💷 budget verdicts</p>
        </div>
        <div class="page-actions">
          <button class="btn ghost" data-dupes>🧹 Find duplicates</button>
        </div>
      </div>

      <div class="toolbar">
        <input type="search" placeholder="Search ingredients…  ( / )" value="${query.search}" data-search>
        <button class="chip click ${query.staplesOnly ? "on" : ""}" data-staples>staples only</button>
        <select data-tier aria-label="Value tier">
          <option value="">any verdict</option>
          <option value="premium" ${query.tier === "premium" ? "selected" : ""}>⭐ premium</option>
          <option value="budget" ${query.tier === "budget" ? "selected" : ""}>💷 budget</option>
        </select>
      </div>

      ${items.length === 0
        ? emptyState("🥕", "Nothing here", "Ingredients appear as recipes and shopping lists use them.")
        : html`
            <div class="card">
              <div class="ing-row ing-head">
                <span class="field-label">name</span><span class="field-label">staple</span>
                <span class="field-label">aisle</span><span></span>
                <span class="field-label">verdict</span><span class="field-label">why</span><span></span>
              </div>
              ${items.map((item) => ingredientRow(item, aisleList))}
            </div>
          `}
    </div>
  `);

  const search = root.querySelector("[data-search]");
  let timer;
  search.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      query.search = search.value.trim();
      renderIngredients(root);
    }, 250);
  };
  root.querySelector("[data-staples]").onclick = () => {
    query.staplesOnly = !query.staplesOnly;
    renderIngredients(root);
  };
  root.querySelector("[data-tier]").onchange = (event) => {
    query.tier = event.target.value;
    renderIngredients(root);
  };
  root.querySelector("[data-dupes]").onclick = () => duplicatesDialog(root);

  for (const row of root.querySelectorAll("[data-ing]")) bindRow(row, root);
}

function ingredientRow(item, aisleList) {
  return html`
    <div class="ing-row" data-ing="${item.id}">
      <span class="name" title="${item.name}">${item.name}</span>
      <input type="checkbox" ${item.is_staple ? "checked" : ""} data-k="is_staple" title="Staples hide from the list until a staples check says you're low">
      <select data-k="aisle" aria-label="Aisle">
        ${aisleList.map((a) => html`<option value="${a.emoji}" ${a.emoji === item.aisle ? "selected" : ""}>${a.emoji} ${a.label}</option>`)}
      </select>
      <span></span>
      <select data-k="value_tier" aria-label="Value verdict">
        <option value="any" ${item.value_tier === "any" ? "selected" : ""}>no opinion</option>
        <option value="premium" ${item.value_tier === "premium" ? "selected" : ""}>⭐ premium</option>
        <option value="budget" ${item.value_tier === "budget" ? "selected" : ""}>💷 budget</option>
      </select>
      <input type="text" value="${item.value_note ?? ""}" placeholder="why (shows at the shelf)" data-k="value_note">
      <button class="icon-btn warm" data-del title="Only unreferenced ingredients can go">✕</button>
    </div>
  `;
}

function bindRow(row, root) {
  const id = row.dataset.ing;
  const save = async (body) => {
    try {
      await api(`/ingredients/${id}`, { method: "PATCH", body });
      row.classList.remove("flash");
      void row.offsetWidth; // restart the saved-flash animation
      row.classList.add("flash");
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };
  row.querySelector('[data-k="is_staple"]').onchange = (e) => save({ is_staple: e.target.checked });
  row.querySelector('[data-k="aisle"]').onchange = (e) => save({ aisle: e.target.value });
  row.querySelector('[data-k="value_tier"]').onchange = (e) => save({ value_tier: e.target.value });
  const note = row.querySelector('[data-k="value_note"]');
  note.onchange = () => save({ value_note: note.value.trim() || null });
  row.querySelector("[data-del]").onclick = async () => {
    const name = row.querySelector(".name").textContent;
    const ok = await confirmDialog({
      title: `Delete “${name}”?`,
      body: "Only works if nothing references it — recipes, meals and list lines all protect their ingredients.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/ingredients/${id}`, { method: "DELETE" });
      toast("Deleted.", "ok");
      renderIngredients(root);
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };
}

async function duplicatesDialog(root) {
  const dialog = openDialog(html`
    <h2>Duplicate ingredients</h2>
    <p class="sub">Same food, different spellings — merging repoints every recipe line and list item at the keeper, then removes the spares.</p>
    <div data-body>${skeleton()}</div>
    <div class="dialog-actions"><button class="btn ghost" data-x>Close</button></div>
  `);
  dialog.querySelector("[data-x]").onclick = () => dialog.close();

  const body = dialog.querySelector("[data-body]");
  const load = async () => {
    const dupes = await api("/ingredients/duplicates");
    if (dupes.groups.length === 0) {
      render(body, html`<div class="empty"><span class="e-emoji">✨</span><h2>Catalogue is clean</h2><p>No duplicate names found.</p></div>`);
      return;
    }
    render(body, html`
      <ul class="picker-list">
        ${dupes.groups.map(
          (group) => html`
            <li>
              <div class="p-main">
                <div class="p-title">${group.keeper.name} <span class="chip green">keeper</span></div>
                <div class="p-sub">absorbs: ${group.duplicates.map((d) => d.name).join(", ")}</div>
              </div>
              <button class="btn small" data-merge="${group.keeper.id}" data-dupes-ids="${group.duplicates.map((d) => d.id).join(",")}">Merge</button>
            </li>
          `,
        )}
      </ul>
    `);
    for (const button of body.querySelectorAll("[data-merge]")) {
      button.onclick = async () => {
        button.disabled = true;
        try {
          const result = await api(`/ingredients/${button.dataset.merge}/merge`, {
            method: "POST",
            body: { duplicate_ids: button.dataset.dupesIds.split(",") },
          });
          toast(`Merged ${result.merged} into “${result.ingredient.name}”.`, "ok");
          await load();
          renderIngredients(root);
        } catch (error) {
          button.disabled = false;
          toast(error.detail || error.message, "error");
        }
      };
    }
  };
  await load();
}
