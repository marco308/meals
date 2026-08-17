// Meals: the thing you actually put on a plan. One or more recipes (with an
// optional scale) plus loose ingredients — "cottage pie with peas & carrots".

import { api } from "../api.js";
import { confirmDialog, debounce, emptyState, fmtRel, foodEmoji, html, render, skeleton, toast } from "../dom.js";
import { linesEditor } from "./lines.js";

let query = { search: "", slot: "" };

// The multiplier behind a portion count, shown next to it because the
// shopping list works in multiples and "×1.5" is how batch cooking is said.
function timesLabel(entry) {
  const scale = (entry.wanted ?? entry.servings) / entry.servings;
  return scale === 1 ? "" : `×${Number(scale.toFixed(2))}`;
}

export async function renderMeals(root) {
  render(root, html`
    <div class="page narrow">
      <div class="page-head">
        <div>
          <h1>Meals</h1>
          <p class="sub" data-count>&nbsp;</p>
        </div>
        <div class="page-actions"><a class="btn" href="#/meals/new">＋ New meal</a></div>
      </div>

      <div class="toolbar">
        <input type="search" placeholder="Search meals…  ( / )" value="${query.search}" data-search>
        <span class="facet-chips" data-slots></span>
      </div>

      <div data-results>${skeleton()}</div>
    </div>
  `);

  // Filter changes redraw the results (and the data-derived count and slot
  // chips) only — the toolbar stays put so typing in the search box never
  // loses the input, its focus, or the caret.
  const results = root.querySelector("[data-results]");
  const slotsBox = root.querySelector("[data-slots]");
  const count = root.querySelector("[data-count]");
  let seq = 0;
  const refresh = async () => {
    const ticket = ++seq;
    try {
      const meals = await api("/meals", {
        query: { search: query.search || undefined, slot: query.slot || undefined },
      });
      if (ticket !== seq) return; // a newer filter overtook this fetch
      count.textContent = `${meals.length} to choose from`;

      const slots = [...new Set(meals.map((m) => m.slot).filter(Boolean))].sort();
      render(slotsBox, html`${slots.map((slot) => html`<button class="chip click ${query.slot === slot ? "on" : ""}" data-slot="${slot}">${slot}</button>`)}`);
      for (const chip of slotsBox.querySelectorAll("[data-slot]")) {
        chip.onclick = () => {
          query.slot = query.slot === chip.dataset.slot ? "" : chip.dataset.slot;
          refresh();
        };
      }

      render(results, meals.length === 0
        ? emptyState("🍽️", "No meals yet", "A meal is a recipe (or a few) with any extras — make one and it becomes a plan option.", html`<a class="btn" href="#/meals/new">＋ New meal</a>`)
        : html`
            <ul class="row-list">
              ${meals.map(
                (meal) => html`
                  <a class="row-card" href="#/meals/${meal.id}">
                    <span class="rc-emoji" aria-hidden="true">${foodEmoji(meal.name)}</span>
                    <div class="rc-main">
                      <div class="rc-title">${meal.name}</div>
                      <div class="rc-sub">
                        ${meal.recipes.map((r) => r.title).join(" + ") || "loose ingredients"}
                        ${meal.loose_ingredients.length > 0 && meal.recipes.length > 0 ? " + extras" : ""}
                      </div>
                    </div>
                    <div class="rc-side">
                      ${meal.slot && html`<span class="chip">${meal.slot}</span>`}
                      ${meal.times_cooked > 0
                        ? html`<span class="chip red">cooked ${meal.times_cooked}×</span>`
                        : html`<span class="chip green">new</span>`}
                    </div>
                  </a>
                `,
              )}
            </ul>
          `);
    } catch (error) {
      if (ticket === seq) toast(error.detail || error.message, "error");
    }
  };

  const search = root.querySelector("[data-search]");
  search.oninput = debounce(() => {
    query.search = search.value.trim();
    refresh();
  });

  await refresh();
}

export async function renderMealDetail(root, mealId) {
  render(root, skeleton());
  const meal = await api(`/meals/${mealId}`);

  render(root, html`
    <div class="page narrow">
      <div class="crumb"><a href="#/meals">← Meals</a></div>
      <div class="page-head">
        <div>
          <h1>${foodEmoji(meal.name)} ${meal.name}</h1>
          <div class="meta-chips">
            ${meal.slot && html`<span class="chip">${meal.slot}</span>`}
            ${meal.times_cooked > 0
              ? html`<span class="chip red">cooked ${meal.times_cooked}× · last ${fmtRel(meal.last_cooked_at)}</span>`
              : html`<span class="chip green">never cooked</span>`}
          </div>
        </div>
        <div class="page-actions">
          <button class="btn" data-to-plan>🍲 Put it on the plan</button>
          <a class="btn ghost" href="#/meals/${meal.id}/edit">Edit</a>
          <button class="btn danger" data-del>Delete</button>
        </div>
      </div>

      ${meal.recipes.length > 0 &&
      html`
        <div class="section">
          <h2>Recipes</h2>
          <ul class="row-list">
            ${meal.recipes.map(
              (recipe) => html`
                <a class="row-card" href="#/recipes/${recipe.id}">
                  <span class="rc-emoji" aria-hidden="true">📖</span>
                  <div class="rc-main">
                    <div class="rc-title">${recipe.title}${recipe.scale && recipe.scale !== 1 ? html` <span class="chip butter">×${recipe.scale}</span>` : ""}</div>
                    <div class="rc-sub">${recipe.scaled_servings ?? recipe.servings ? `serves ${recipe.scaled_servings ?? recipe.servings}` : ""}${recipe.times_cooked > 0 ? ` · cooked ${recipe.times_cooked}×` : ""}</div>
                  </div>
                </a>
              `,
            )}
          </ul>
        </div>
      `}

      ${meal.loose_ingredients.length > 0 &&
      html`
        <div class="section">
          <h2>Extras</h2>
          <div class="card">
            <ul class="ing-list">
              ${meal.loose_ingredients.map(
                (line) => html`<li><span class="qty">${line.display}</span><span>${line.aisle} ${line.name}</span></li>`,
              )}
            </ul>
          </div>
        </div>
      `}
    </div>
  `);

  root.querySelector("[data-to-plan]").onclick = async () => {
    let plan;
    try {
      plan = await api("/plans/current");
    } catch (error) {
      if (error.status !== 404) throw error;
      plan = await api("/plans", { method: "POST", body: { label: "This week" } });
    }
    try {
      await api(`/plans/${plan.id}/meals`, { method: "POST", body: { meal_id: meal.id } });
      toast("On the plan — ingredients joined the shopping list.", "ok");
    } catch (error) {
      if (error.status === 409) toast("Already on the plan.", "ok");
      else toast(error.detail || error.message, "error");
    }
  };

  root.querySelector("[data-del]").onclick = async () => {
    const ok = await confirmDialog({
      title: `Delete “${meal.name}”?`,
      body: "It comes off any plan it is on, and its share leaves the shopping list. Recipes stay in the library; cooked history stays on the record.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    await api(`/meals/${meal.id}`, { method: "DELETE" });
    toast("Meal deleted.", "ok");
    window.location.hash = "#/meals";
  };
}

export async function renderMealEditor(root, mealId) {
  render(root, skeleton());
  const meal = mealId ? await api(`/meals/${mealId}`) : null;
  // `servings` is the recipe's own figure and never changes; `wanted` is how
  // many this meal is for. When the recipe says how many it serves we ask in
  // portions and let the server do the division (issue #53) — a multiplier is
  // only the right question when there's nothing to divide by.
  let picked = meal
    ? meal.recipes.map((r) => ({
        recipe_id: r.id,
        title: r.title,
        servings: r.servings ?? null,
        scale: r.scale ?? 1,
        wanted: r.scaled_servings ?? null,
      }))
    : [];

  render(root, html`
    <div class="page narrow">
      <div class="crumb"><a href="${meal ? `#/meals/${meal.id}` : "#/meals"}">← ${meal ? meal.name : "Meals"}</a></div>
      <div class="page-head"><h1>${meal ? "Edit meal" : "New meal"}</h1></div>
      <form class="card" data-f>
        <div class="form-row">
          <label class="field"><span>Name</span>
            <input type="text" name="name" required value="${meal?.name ?? ""}" placeholder="Cottage pie with peas"></label>
          <label class="field"><span>Slot (optional)</span>
            <input type="text" name="slot" value="${meal?.slot ?? ""}" list="slot-suggestions" placeholder="dinner">
            <datalist id="slot-suggestions"><option value="dinner"><option value="lunch"><option value="breakfast"></datalist></label>
        </div>

        <div class="field-label">Recipes</div>
        <div data-picked></div>
        <input type="search" placeholder="Search the library to add a recipe…" data-recipe-q autocomplete="off">
        <ul class="picker-list" data-recipe-results></ul>

        <div class="field-label" data-extras-label>Extras (loose ingredients — the peas and carrots)</div>
        <div data-lines></div>

        <div class="dialog-actions">
          <a class="btn ghost" href="${meal ? `#/meals/${meal.id}` : "#/meals"}">Cancel</a>
          <button class="btn" type="submit">${meal ? "Save changes" : "Create meal"}</button>
        </div>
      </form>
    </div>
  `);

  const pickedBox = root.querySelector("[data-picked]");
  const drawPicked = () => {
    render(pickedBox, html`
      ${picked.length === 0 && html`<p class="sub">No recipes — a meal can be loose ingredients alone.</p>`}
      <ul class="picker-list">
        ${picked.map(
          (entry, index) => html`
            <li>
              <span aria-hidden="true">📖</span>
              <div class="p-main"><div class="p-title">${entry.title}</div></div>
              ${entry.servings
                ? html`<label class="check-line" title="How many this meal is for — the recipe serves ${entry.servings}">serves
                    <input type="number" step="1" min="1" max="100" value="${entry.wanted ?? entry.servings}" data-wanted="${index}" class="scale-input">
                    <span class="sub" data-times="${index}">${timesLabel(entry)}</span></label>`
                : html`<label class="check-line" title="Scale — ×2 for batch cooking">×
                    <input type="number" step="0.5" min="0.5" value="${entry.scale}" data-scale="${index}" class="scale-input"></label>`}
              <button type="button" class="icon-btn warm" data-unpick="${index}">remove</button>
            </li>
          `,
        )}
      </ul>
    `);
    for (const input of pickedBox.querySelectorAll("[data-scale]")) {
      input.oninput = () => {
        picked[Number(input.dataset.scale)].scale = Number(input.value) || 1;
      };
    }
    for (const input of pickedBox.querySelectorAll("[data-wanted]")) {
      input.oninput = () => {
        const index = Number(input.dataset.wanted);
        const entry = picked[index];
        entry.wanted = Number(input.value) || entry.servings;
        // Keep the multiplier visible: batch cooking is still how people talk
        // about it, and it's the number the shopping list actually uses.
        pickedBox.querySelector(`[data-times="${index}"]`).textContent = timesLabel(entry);
      };
    }
    for (const button of pickedBox.querySelectorAll("[data-unpick]")) {
      button.onclick = () => {
        picked.splice(Number(button.dataset.unpick), 1);
        drawPicked();
      };
    }
  };
  drawPicked();

  const results = root.querySelector("[data-recipe-results]");
  const recipeQuery = root.querySelector("[data-recipe-q]");
  const searchRecipes = async () => {
    if (!recipeQuery.value.trim()) {
      render(results, "");
      return;
    }
    const recipes = await api("/recipes", { query: { search: recipeQuery.value.trim() } });
    render(results, html`
      ${recipes.length === 0 && html`<li><span class="p-sub">Nothing in the library matches.</span></li>`}
      ${recipes.slice(0, 6).map((recipe) => {
        const already = picked.some((p) => p.recipe_id === recipe.id);
        return html`
          <li>
            <span aria-hidden="true">📖</span>
            <div class="p-main"><div class="p-title">${recipe.title}</div></div>
            ${already ? html`<span class="chip green">added</span>` : html`<button type="button" class="btn small" data-add-recipe="${recipe.id}" data-title="${recipe.title}" data-servings="${recipe.servings ?? ""}">Add</button>`}
          </li>
        `;
      })}
    `);
    for (const button of results.querySelectorAll("[data-add-recipe]")) {
      button.onclick = () => {
        const servings = Number(button.dataset.servings) || null;
        picked.push({
          recipe_id: button.dataset.addRecipe,
          title: button.dataset.title,
          servings,
          scale: 1,
          wanted: servings,
        });
        recipeQuery.value = "";
        render(results, "");
        drawPicked();
      };
    }
  };
  let timer;
  recipeQuery.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(searchRecipes, 250);
  };

  const lines = linesEditor(root.querySelector("[data-lines]"), meal?.loose_ingredients ?? []);

  root.querySelector("[data-f]").onsubmit = async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target));
    const body = {
      name: data.name.trim(),
      slot: data.slot.trim() || null,
      // One key or the other per recipe — the API refuses both together.
      recipes: picked.map(({ recipe_id, servings, scale, wanted }) =>
        servings ? { recipe_id, servings: wanted ?? servings } : { recipe_id, scale },
      ),
      loose_ingredients: lines.read(),
    };
    try {
      const saved = meal
        ? await api(`/meals/${meal.id}`, { method: "PATCH", body })
        : await api("/meals", { method: "POST", body });
      toast(meal ? "Saved — any plan using it resyncs the list." : "Meal created.", "ok");
      window.location.hash = `#/meals/${saved.id}`;
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };
}
