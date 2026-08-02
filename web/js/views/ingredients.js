// The ingredient catalogue: aisles, staples and the household's own
// premium/budget verdicts (Q17 — never guessed, so this is where they get
// set). Also the duplicates clean-up (Q21), which finally gets a screen —
// until now only an AI over MCP could run it. The finder only reports
// name-folding facts, so each row also carries a manual "merge…" for the
// pairs it can't claim ("beef mince" vs "minced beef").

import { aisles, api } from "../api.js";
import { confirmDialog, debounce, emptyState, html, openDialog, render, skeleton, toast } from "../dom.js";

let query = { search: "", staplesOnly: false, tier: "" };

export async function renderIngredients(root) {
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

      <div data-results>${skeleton()}</div>
    </div>
  `);

  // Filter changes redraw [data-results] only — the toolbar (and the search
  // input mid-typing) must survive, so never re-render the whole page here.
  const results = root.querySelector("[data-results]");
  let seq = 0;
  const refresh = async () => {
    const ticket = ++seq;
    try {
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
      if (ticket !== seq) return; // a newer filter overtook this fetch
      render(results, items.length === 0
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
          `);
      for (const row of results.querySelectorAll("[data-ing]")) bindRow(row, refresh);
    } catch (error) {
      if (ticket === seq) toast(error.detail || error.message, "error");
    }
  };

  const search = root.querySelector("[data-search]");
  search.oninput = debounce(() => {
    query.search = search.value.trim();
    refresh();
  });
  const staplesChip = root.querySelector("[data-staples]");
  staplesChip.onclick = () => {
    query.staplesOnly = !query.staplesOnly;
    staplesChip.classList.toggle("on", query.staplesOnly);
    refresh();
  };
  root.querySelector("[data-tier]").onchange = (event) => {
    query.tier = event.target.value;
    refresh();
  };
  root.querySelector("[data-dupes]").onclick = () => duplicatesDialog(refresh);

  await refresh();
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
      <span class="row-actions">
        <button class="icon-btn" data-mrg title="Fold this into another ingredient — for duplicates the finder can't see">merge…</button>
        <button class="icon-btn warm" data-del title="Only unreferenced ingredients can go">✕</button>
      </span>
    </div>
  `;
}

function bindRow(row, refresh) {
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
  row.querySelector("[data-mrg]").onclick = () => {
    mergeIntoDialog({ id, name: row.querySelector(".name").textContent }, refresh);
  };
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
      refresh();
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };
}

// One line of curation context per ingredient, so a merge choice is made
// knowing whose aisle/staple/verdict survives (the keeper's) and whose is lost.
const ingredientMeta = (item) =>
  [
    `${item.aisle} ${item.aisle_label}`,
    item.is_staple && "staple",
    item.value_tier === "premium" && "⭐ premium",
    item.value_tier === "budget" && "💷 budget",
    item.value_note && `“${item.value_note}”`,
  ]
    .filter(Boolean)
    .join(" · ");

async function duplicatesDialog(refresh) {
  const dialog = openDialog(html`
    <h2>Duplicate ingredients</h2>
    <p class="sub">Same food, different spellings. Pick the one that survives — merging repoints every recipe line and list item at it, then removes the rest. The survivor keeps its own aisle, staple flag and verdict.</p>
    <div data-body>${skeleton()}</div>
    <div class="dialog-actions"><button class="btn ghost" data-x>Close</button></div>
  `);
  dialog.querySelector("[data-x]").onclick = () => dialog.close();

  const body = dialog.querySelector("[data-body]");
  const load = async () => {
    const dupes = await api("/ingredients/duplicates");
    if (dupes.groups.length === 0 && dupes.unfolded.length === 0) {
      render(body, html`<div class="empty"><span class="e-emoji">✨</span><h2>Catalogue is clean</h2>
        <p>No duplicate names found. Spotted a pair this can't see, like “beef mince” and “minced beef”? Use merge… on the ingredient's own row.</p></div>`);
      return;
    }
    render(body, html`
      ${dupes.groups.map((group, index) => {
        const members = [group.keeper, ...group.duplicates];
        return html`
          <section class="merge-group" data-group="${index}">
            ${members.map(
              (member) => html`
                <label class="merge-member">
                  <input type="radio" name="keep-${index}" value="${member.id}" ${member.id === group.keeper.id ? "checked" : ""}>
                  <span class="m-name">${member.name}</span>
                  ${member.id === group.keeper.id ? html`<span class="chip green">suggested</span>` : null}
                  <span class="m-meta">${ingredientMeta(member)}</span>
                </label>
              `,
            )}
            <div class="merge-group-foot">
              <span class="m-hint" data-hint hidden>new recipes file this food under “${group.canonical_name}”, so a separate “${group.canonical_name}” can creep back</span>
              <button class="btn small" data-merge></button>
            </div>
          </section>
        `;
      })}
      ${dupes.unfolded.length > 0 &&
      html`
        <section class="unfolded">
          <h3 class="merge-section-head">Old spellings</h3>
          <p class="sub">Stored under a name a new recipe wouldn't use, with nothing to merge into. Filing one moves it to the modern name, keeping its aisle, staple flag and verdict.</p>
          <ul class="picker-list">
            ${dupes.unfolded.map(
              (entry, index) => html`
                <li>
                  <div class="p-main">
                    <div class="p-title">${entry.ingredient.name}</div>
                    <div class="p-sub">${ingredientMeta(entry.ingredient)}</div>
                  </div>
                  <button class="btn small" data-unfold="${index}">File under “${entry.canonical_name}”</button>
                </li>
              `,
            )}
          </ul>
        </section>
      `}
    `);
    for (const section of body.querySelectorAll("[data-group]")) {
      const group = dupes.groups[Number(section.dataset.group)];
      const members = [group.keeper, ...group.duplicates];
      const button = section.querySelector("[data-merge]");
      const hint = section.querySelector("[data-hint]");
      const picked = () => members.find((member) => member.id === section.querySelector("input:checked").value);
      const sync = () => {
        const keep = picked();
        button.textContent = `Merge ${members.length - 1} into “${keep.name}”`;
        hint.hidden = keep.name === group.canonical_name;
      };
      for (const radio of section.querySelectorAll("input[type=radio]")) radio.onchange = sync;
      sync();
      button.onclick = async () => {
        const keep = picked();
        button.disabled = true;
        try {
          const result = await api(`/ingredients/${keep.id}/merge`, {
            method: "POST",
            body: { duplicate_ids: members.filter((member) => member.id !== keep.id).map((member) => member.id) },
          });
          toast(`Merged ${result.merged} into “${result.ingredient.name}”.`, "ok");
          await load();
          refresh();
        } catch (error) {
          button.disabled = false;
          toast(error.detail || error.message, "error");
        }
      };
    }
    for (const button of body.querySelectorAll("[data-unfold]")) {
      button.onclick = async () => {
        const entry = dupes.unfolded[Number(button.dataset.unfold)];
        button.disabled = true;
        try {
          // The canonical row doesn't exist yet (that is what made this row
          // "unfolded"), so create it carrying the old row's curation — this
          // is a rename, not a merge of two opinions — then fold the old row
          // into it.
          const keeper = await api("/ingredients", {
            method: "POST",
            body: {
              name: entry.canonical_name,
              aisle: entry.ingredient.aisle,
              is_staple: entry.ingredient.is_staple,
              value_tier: entry.ingredient.value_tier,
              value_note: entry.ingredient.value_note,
            },
          });
          await api(`/ingredients/${keeper.id}/merge`, {
            method: "POST",
            body: { duplicate_ids: [entry.ingredient.id] },
          });
          toast(`Filed “${entry.ingredient.name}” under “${keeper.name}”.`, "ok");
          await load();
          refresh();
        } catch (error) {
          button.disabled = false;
          toast(error.detail || error.message, "error");
        }
      };
    }
  };
  await load();
}

// Manual merge for the pairs the duplicates finder deliberately won't claim
// (Q21: it reports name-folding facts, not guesses). The human supplies the
// judgement the table abstains from; the survivor they pick keeps its name.
async function mergeIntoDialog(source, refresh) {
  const dialog = openDialog(html`
    <h2>Merge “${source.name}” into…</h2>
    <p class="sub">Pick the ingredient that survives. Every recipe line, meal and shopping-list line pointing at “${source.name}” moves onto it, then “${source.name}” is deleted. Not reversible — merge spellings of the same food, not things bought together.</p>
    <input type="search" placeholder="Search ingredients…" data-q autocomplete="off">
    <ul class="picker-list" data-results>${skeleton()}</ul>
  `);

  const results = dialog.querySelector("[data-results]");
  const input = dialog.querySelector("[data-q]");
  const search = async () => {
    const items = (await api("/ingredients", { query: { search: input.value.trim() || undefined } })).filter(
      (item) => item.id !== source.id,
    );
    render(results, html`
      ${items.length === 0 && html`<li><span class="p-sub">No other ingredient matches.</span></li>`}
      ${items.map(
        (item) => html`
          <li>
            <div class="p-main">
              <div class="p-title">${item.name}</div>
              <div class="p-sub">${ingredientMeta(item)}</div>
            </div>
            <button class="btn small" data-pick="${item.id}" data-name="${item.name}">Merge into this</button>
          </li>
        `,
      )}
    `);
    for (const button of results.querySelectorAll("[data-pick]")) {
      button.onclick = async () => {
        const ok = await confirmDialog({
          title: `Merge “${source.name}” into “${button.dataset.name}”?`,
          body: `Everything pointing at “${source.name}” moves onto “${button.dataset.name}”, then “${source.name}” is deleted. This can't be undone.`,
          confirmLabel: "Merge",
          danger: true,
        });
        if (!ok) return;
        button.disabled = true;
        try {
          const result = await api(`/ingredients/${button.dataset.pick}/merge`, {
            method: "POST",
            body: { duplicate_ids: [source.id] },
          });
          dialog.close();
          toast(`Merged “${source.name}” into “${result.ingredient.name}”.`, "ok");
          refresh();
        } catch (error) {
          button.disabled = false;
          toast(error.detail || error.message, "error");
        }
      };
    }
  };
  let timer;
  input.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(search, 220);
  };
  await search();
  input.focus();
}
