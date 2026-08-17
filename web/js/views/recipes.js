// The recipe library. Ingest-by-URL is the headline: paste a link, the server
// pulls the page's schema.org JSON-LD (parse once, reuse forever — Q3), and
// the recipe lands in the library with its lines already on the unit
// convention. Pages without usable JSON-LD 422 with advice we show verbatim.

import { api } from "../api.js";
import { confirmDialog, debounce, emptyState, fmtRel, html, openDialog, render, skeleton, toast } from "../dom.js";
import { linesEditor } from "./lines.js";

let query = { search: "", tag: "", sort: "title", under30: false };

export async function renderRecipes(root) {
  render(root, html`
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Recipes</h1>
          <p class="sub" data-count>&nbsp;</p>
        </div>
        <div class="page-actions">
          <button class="btn" data-ingest>🔗 From a URL</button>
          <a class="btn ghost" href="#/recipes/new">Write one in</a>
        </div>
      </div>

      <div class="toolbar">
        <input type="search" placeholder="Search recipes…  ( / )" value="${query.search}" data-search>
        <select data-sort aria-label="Sort">
          <option value="title" ${query.sort === "title" ? "selected" : ""}>A → Z</option>
          <option value="most_cooked" ${query.sort === "most_cooked" ? "selected" : ""}>Most cooked</option>
          <option value="least_recently_cooked" ${query.sort === "least_recently_cooked" ? "selected" : ""}>Not had in a while</option>
        </select>
        <button class="chip click ${query.under30 ? "on" : ""}" data-under30>under 30 min</button>
        <span class="gap"></span>
        <span class="facet-chips" data-tags></span>
      </div>

      <div data-results>${skeleton()}</div>
    </div>
  `);

  // Filter changes redraw the results (and the data-derived count and tag
  // chips) only — the toolbar stays put so typing in the search box never
  // loses the input, its focus, or the caret.
  const results = root.querySelector("[data-results]");
  const tagsBox = root.querySelector("[data-tags]");
  const count = root.querySelector("[data-count]");
  let seq = 0;
  const refresh = async () => {
    const ticket = ++seq;
    try {
      const recipes = await api("/recipes", {
        query: {
          search: query.search || undefined,
          tag: query.tag || undefined,
          sort: query.sort,
          max_total_minutes: query.under30 ? 30 : undefined,
        },
      });
      if (ticket !== seq) return; // a newer filter overtook this fetch
      count.textContent = `${recipes.length} in the library`;

      const tags = [...new Set(recipes.flatMap((r) => r.tags))].sort();
      render(tagsBox, html`${tags.map((tag) => html`<button class="chip click ${query.tag === tag ? "on" : ""}" data-tag="${tag}">${tag}</button>`)}`);
      for (const chip of tagsBox.querySelectorAll("[data-tag]")) {
        chip.onclick = () => {
          query.tag = query.tag === chip.dataset.tag ? "" : chip.dataset.tag;
          refresh();
        };
      }

      render(results, recipes.length === 0
        ? emptyState("📖", "No recipes yet", "Paste a recipe URL and the library starts itself — or ask your AI to fill it while you put the kettle on.", html`<button class="btn" data-ingest-empty>🔗 Add one from a URL</button>`)
        : html`
            <div class="recipe-grid">
              ${recipes.map(
                (recipe) => html`
                  <a class="recipe-card" href="#/recipes/${recipe.id}">
                    <div class="r-img">${recipe.image_url ? html`<img src="${recipe.image_url}" alt="" loading="lazy">` : "🍳"}</div>
                    <div class="r-body">
                      <div class="r-title">${recipe.title}</div>
                      <div class="r-meta">
                        ${totalMinutes(recipe) !== null && html`<span>⏱ ${totalMinutes(recipe)} min</span>`}
                        ${recipe.servings > 0 && html`<span>serves ${recipe.servings}</span>`}
                        ${recipe.times_cooked > 0
                          ? html`<span>cooked ${recipe.times_cooked}×</span>`
                          : html`<span>never cooked</span>`}
                      </div>
                    </div>
                  </a>
                `,
              )}
            </div>
          `);
      const ingestEmpty = results.querySelector("[data-ingest-empty]");
      if (ingestEmpty) ingestEmpty.onclick = () => ingestDialog();
    } catch (error) {
      if (ticket === seq) toast(error.detail || error.message, "error");
    }
  };

  const search = root.querySelector("[data-search]");
  search.oninput = debounce(() => {
    query.search = search.value.trim();
    refresh();
  });
  root.querySelector("[data-sort]").onchange = (event) => {
    query.sort = event.target.value;
    refresh();
  };
  const under30 = root.querySelector("[data-under30]");
  under30.onclick = () => {
    query.under30 = !query.under30;
    under30.classList.toggle("on", query.under30);
    refresh();
  };
  root.querySelector("[data-ingest]").onclick = () => ingestDialog();

  await refresh();
}

const totalMinutes = (recipe) => (recipe.prep_minutes || 0) + (recipe.cook_minutes || 0) || null;

function ingestDialog() {
  const dialog = openDialog(html`
    <h2>Recipe from a URL</h2>
    <p class="sub">Most recipe sites carry machine-readable data; if this one does, the whole thing lands in one go. Already-known URLs come back from the library instead of duplicating.</p>
    <form data-f>
      <label class="field"><span>Recipe page</span>
        <input type="url" name="url" required placeholder="https://www.bbcgoodfood.com/recipes/…" autofocus></label>
      <div data-msg></div>
      <div class="dialog-actions">
        <button class="btn ghost" type="button" data-x>Cancel</button>
        <button class="btn" type="submit">Fetch it</button>
      </div>
    </form>
  `);
  dialog.querySelector("[data-x]").onclick = () => dialog.close();
  dialog.querySelector("[data-f]").onsubmit = async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button[type=submit]");
    const msg = dialog.querySelector("[data-msg]");
    button.disabled = true;
    button.textContent = "Fetching…";
    try {
      const result = await api("/recipes/ingest", { method: "POST", body: { url: event.target.url.value.trim() } });
      dialog.close();
      toast(result.cached ? "Already in the library — took you there." : `“${result.recipe.title}” landed.`, "ok");
      window.location.hash = `#/recipes/${result.recipe.id}`;
    } catch (error) {
      button.disabled = false;
      button.textContent = "Fetch it";
      render(msg, html`<div class="staples-banner">${error.detail || error.message}
        ${error.status === 422 && html`<br><br>You can still <a href="#/recipes/new" data-manual>write it in by hand</a> — or paste the page at your AI, which can read what the site didn't mark up.`}</div>`);
      const manual = msg.querySelector("[data-manual]");
      if (manual) manual.onclick = () => dialog.close();
    }
  };
}

export async function renderRecipeDetail(root, recipeId) {
  render(root, skeleton());
  const recipe = await api(`/recipes/${recipeId}`);
  const total = totalMinutes(recipe);

  render(root, html`
    <div class="page">
      <div class="crumb"><a href="#/recipes">← Recipes</a></div>
      <div class="page-head">
        <div>
          <h1>${recipe.title}</h1>
          <div class="meta-chips">
            ${recipe.servings > 0 && html`<span class="chip">serves ${recipe.servings}</span>`}
            ${recipe.prep_minutes > 0 && html`<span class="chip">prep ${recipe.prep_minutes} min</span>`}
            ${recipe.cook_minutes > 0 && html`<span class="chip">cook ${recipe.cook_minutes} min</span>`}
            ${total !== null && html`<span class="chip butter">⏱ ${total} min all in</span>`}
            ${recipe.times_cooked > 0
              ? html`<span class="chip red">cooked ${recipe.times_cooked}× · last ${fmtRel(recipe.last_cooked_at)}</span>`
              : html`<span class="chip green">never cooked</span>`}
            ${recipe.tags.map((tag) => html`<span class="chip">${tag}</span>`)}
            ${recipe.edited && html`<span class="chip">edited</span>`}
          </div>
        </div>
        <div class="page-actions">
          <button class="btn" data-plan-it>🍲 Put it on the plan</button>
          <a class="btn ghost" href="#/recipes/${recipe.id}/edit">Edit</a>
          ${recipe.source_url
            ? html`<button class="btn ghost" data-reparse title="Read the original page again — for when the site has corrected it">↻ Re-read the page</button>`
            : ""}
          <button class="btn danger" data-del>Delete</button>
        </div>
      </div>

      ${recipe.image_url && html`<div class="hero-img"><img src="${recipe.image_url}" alt="${recipe.title}"></div>`}

      <div class="detail-cols">
        <div class="card">
          <h2>Ingredients</h2>
          <ul class="ing-list">
            ${recipe.ingredients.map(
              (line) => html`
                <li>
                  <span class="qty">${line.display}</span>
                  <span>${line.aisle} ${line.name}${line.value_tier === "premium" ? html` <span class="value-badge" title="${line.value_note || "worth paying up"}">⭐</span>` : ""}${line.value_tier === "budget" ? html` <span class="value-badge" title="${line.value_note || "own-brand is fine"}">💷</span>` : ""}</span>
                </li>
              `,
            )}
          </ul>
        </div>
        <div>
          ${recipe.source_url &&
          html`<p class="sub">From <a href="${recipe.source_url}" target="_blank" rel="noopener noreferrer">${hostOf(recipe.source_url)}</a>${recipe.parse_source === "ai" ? " · parsed by an AI" : ""}</p>`}
          <div class="prose">${instructions(recipe.instructions)}</div>
        </div>
      </div>
    </div>
  `);

  root.querySelector("[data-del]").onclick = async () => {
    const ok = await confirmDialog({
      title: `Delete “${recipe.title}”?`,
      body: "Gone from the library for the whole household. Its cooked history stays on the record.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/recipes/${recipe.id}`, { method: "DELETE" });
      toast("Recipe deleted.", "ok");
      window.location.hash = "#/recipes";
    } catch (error) {
      toast(error.detail || error.message, "error"); // 409: still used by meals
    }
  };

  const reparseButton = root.querySelector("[data-reparse]");
  if (reparseButton) reparseButton.onclick = () => reparse(recipe, root);

  root.querySelector("[data-plan-it]").onclick = () => planIt(recipe);
}

// Parse once, reuse forever (Q3) means nothing ever re-fetches a recipe. This
// is the deliberate exception, for a source page that has been corrected. The
// server refuses an edited recipe with a 409, so the force confirmation is
// asked only of the people who actually have edits to lose.
async function reparse(recipe, root) {
  const run = async (force) => {
    const fresh = await api(`/recipes/${recipe.id}/reparse`, { method: "POST", body: { force } });
    toast(`Re-read from ${hostOf(recipe.source_url)}.`, "ok");
    renderRecipeDetail(root, fresh.id);
  };
  try {
    await run(false);
  } catch (error) {
    if (error.status !== 409) {
      toast(error.detail || error.message, "error");
      return;
    }
    const ok = await confirmDialog({
      title: `Replace your edits to “${recipe.title}”?`,
      body: `This recipe has been edited here. Re-reading ${hostOf(recipe.source_url)} replaces the title, ingredients and method with whatever the page says now, and your corrections are gone.`,
      confirmLabel: "Re-read anyway",
      danger: true,
    });
    if (!ok) return;
    try {
      await run(true);
    } catch (retryError) {
      toast(retryError.detail || retryError.message, "error");
    }
  }
}

function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

// instructions are free text; if most lines are numbered steps, render a list
function instructions(text) {
  if (!text) return html`<p class="sub">No method written down — the source link has it.</p>`;
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  const numbered = lines.filter((l) => /^\d+[.)]\s/.test(l));
  if (lines.length > 1 && numbered.length >= lines.length / 2) {
    return html`<ol>${lines.map((l) => html`<li>${l.replace(/^\d+[.)]\s*/, "")}</li>`)}</ol>`;
  }
  return lines.map((l) => html`<p>${l}</p>`);
}

// The core loop, one click: recipe → meal (reusing one named after it) →
// current plan (creating one if the household has none).
async function planIt(recipe) {
  const meals = await api("/meals", { query: { search: recipe.title } });
  let meal = meals.find((m) => m.name.toLowerCase() === recipe.title.toLowerCase());
  meal ||= await api("/meals", { method: "POST", body: { name: recipe.title, slot: "dinner", recipes: [{ recipe_id: recipe.id }] } });

  let plan;
  try {
    plan = await api("/plans/current");
  } catch (error) {
    if (error.status !== 404) throw error;
    plan = await api("/plans", { method: "POST", body: { label: "This week" } });
  }
  try {
    await api(`/plans/${plan.id}/meals`, { method: "POST", body: { meal_id: meal.id } });
    toast(`On the plan — its ingredients just joined the shopping list.`, "ok");
  } catch (error) {
    if (error.status === 409) toast("Already on the plan.", "ok");
    else throw error;
  }
}

export async function renderRecipeEditor(root, recipeId) {
  render(root, skeleton());
  const recipe = recipeId ? await api(`/recipes/${recipeId}`) : null;

  render(root, html`
    <div class="page narrow">
      <div class="crumb"><a href="${recipe ? `#/recipes/${recipe.id}` : "#/recipes"}">← ${recipe ? recipe.title : "Recipes"}</a></div>
      <div class="page-head"><h1>${recipe ? "Edit recipe" : "Write a recipe in"}</h1></div>
      <form class="card" data-f>
        <label class="field"><span>Title</span>
          <input type="text" name="title" required value="${recipe?.title ?? ""}"></label>
        <div class="form-row">
          <label class="field"><span>Serves</span>
            <input type="number" name="servings" min="1" value="${recipe?.servings ?? ""}"></label>
          <label class="field"><span>Prep (min)</span>
            <input type="number" name="prep_minutes" min="0" value="${recipe?.prep_minutes ?? ""}"></label>
          <label class="field"><span>Cook (min)</span>
            <input type="number" name="cook_minutes" min="0" value="${recipe?.cook_minutes ?? ""}"></label>
        </div>
        <label class="field"><span>Tags (comma-separated)</span>
          <input type="text" name="tags" value="${recipe?.tags?.join(", ") ?? ""}" placeholder="italian, family favourite"></label>
        <label class="field"><span>Image URL (optional)</span>
          <input type="url" name="image_url" value="${recipe?.image_url ?? ""}"></label>

        <div class="field-label">Ingredients</div>
        <div data-lines></div>

        <label class="field" for="instructions"><span>Method</span>
          <textarea name="instructions" id="instructions" placeholder="1. Brown the mince.&#10;2. …">${recipe?.instructions ?? ""}</textarea></label>

        <div class="dialog-actions">
          <a class="btn ghost" href="${recipe ? `#/recipes/${recipe.id}` : "#/recipes"}">Cancel</a>
          <button class="btn" type="submit">${recipe ? "Save changes" : "Add to the library"}</button>
        </div>
      </form>
    </div>
  `);

  const lines = linesEditor(root.querySelector("[data-lines]"), recipe?.ingredients ?? []);

  root.querySelector("[data-f]").onsubmit = async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target));
    const body = {
      title: data.title.trim(),
      servings: data.servings ? Number(data.servings) : null,
      prep_minutes: data.prep_minutes ? Number(data.prep_minutes) : null,
      cook_minutes: data.cook_minutes ? Number(data.cook_minutes) : null,
      image_url: data.image_url.trim() || null,
      instructions: data.instructions.trim() || null,
      tags: data.tags.split(",").map((t) => t.trim()).filter(Boolean),
      ingredients: lines.read(),
    };
    try {
      const saved = recipe
        ? await api(`/recipes/${recipe.id}`, { method: "PATCH", body })
        : await api("/recipes", { method: "POST", body });
      toast(recipe ? "Saved — meals using it resync their list lines." : "In the library.", "ok");
      window.location.hash = `#/recipes/${saved.id}`;
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };
}
