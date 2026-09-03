// The freezer: a running tab of cooked portions waiting to be eaten (Q24).
// One row per batch, oldest at the top because that is the one to eat next.
// Nothing here touches the plan or the list — freezing is a statement about
// the freezer, and eating from it is not a cooking.

import { api } from "../api.js";
import { confirmDialog, emptyState, fmtRel, foodEmoji, html, openDialog, render, skeleton, toast } from "../dom.js";

// frozen_on is a bare date; give the relative formatter a midday so the
// UTC-stamping in parseUtc can't roll it onto the day before.
const dateRel = (iso) => fmtRel(`${iso}T12:00:00`);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local

export async function renderFreezer(root) {
  render(root, skeleton());
  const stock = await api("/freezer");
  const items = stock.items;

  render(root, html`
    <div class="page narrow">
      <div class="page-head">
        <div>
          <h1>The freezer</h1>
          <p class="sub">
            ${items.length === 0
              ? "what's already cooked and waiting"
              : `${plural(stock.total_portions, "portion", "portions")} in ${plural(items.length, "batch", "batches")} · oldest at the top, eat that one first`}
          </p>
        </div>
        <div class="page-actions">
          <button class="btn" data-add>＋ Put something in</button>
        </div>
      </div>

      ${items.length === 0
        ? emptyState(
            "❄️",
            "Nothing in the freezer",
            "When you batch-cook, put the spare portions here and this page becomes the answer to “what's for tea?”.",
          )
        : html`<ul class="row-list">${items.map(batch)}</ul>`}
    </div>
  `);

  root.querySelector("[data-add]").onclick = () => addDialog(root);

  for (const button of root.querySelectorAll("[data-take]")) {
    button.onclick = async () => {
      button.disabled = true;
      try {
        const left = await api(`/freezer/${button.dataset.take}/take`, { method: "POST", body: { portions: 1 } });
        toast(
          left.portions === 0
            ? `That was the last of the ${left.label}.`
            : `One out — ${plural(left.portions, "portion", "portions")} of ${left.label} left.`,
          "ok",
        );
        renderFreezer(root);
      } catch (error) {
        button.disabled = false;
        toast(error.detail || error.message, "error");
      }
    };
  }
  for (const button of root.querySelectorAll("[data-more]")) {
    button.onclick = async () => {
      const item = items.find((i) => i.id === button.dataset.more);
      try {
        await api(`/freezer/${item.id}`, { method: "PATCH", body: { portions: item.portions + 1 } });
        renderFreezer(root);
      } catch (error) {
        toast(error.detail || error.message, "error");
      }
    };
  }
  for (const button of root.querySelectorAll("[data-remove]")) {
    button.onclick = async () => {
      const item = items.find((i) => i.id === button.dataset.remove);
      const ok = await confirmDialog({
        title: `Take out the ${item.label}?`,
        body: `${plural(item.portions, "portion", "portions")} come off the tab — for something binned, given away, or that was never there. Eating one is the −1 button.`,
        confirmLabel: "Take it out",
        danger: true,
      });
      if (!ok) return;
      await api(`/freezer/${item.id}`, { method: "DELETE" });
      toast(`${item.label} is out of the freezer.`, "ok");
      renderFreezer(root);
    };
  }
}

function batch(item) {
  const age = (Date.now() - new Date(`${item.frozen_on}T12:00:00`)) / 86_400_000;
  const name = item.meal_id
    ? html`<a href="#/meals/${item.meal_id}">${item.label}</a>`
    : item.recipe_id
      ? html`<a href="#/recipes/${item.recipe_id}">${item.label}</a>`
      : item.label;
  return html`
    <li class="row-card">
      <span class="rc-emoji" aria-hidden="true">${foodEmoji(item.label)}</span>
      <div class="rc-main">
        <div class="rc-title">${name}</div>
        <div class="rc-sub">
          frozen ${dateRel(item.frozen_on)}${item.note && ` · ${item.note}`}
          ${!item.meal_id && !item.recipe_id && " · not from a recipe here"}
        </div>
      </div>
      <div class="rc-side">
        ${age > 90 && html`<span class="chip butter">been in a while</span>`}
        <span class="chip ${item.portions === 1 ? "red" : "green"}">${plural(item.portions, "portion", "portions")}</span>
        <div class="row-actions">
          <button class="icon-btn" data-take="${item.id}" title="Took one out to eat">−1</button>
          <button class="icon-btn" data-more="${item.id}" title="Recount: there's one more than I said">+1</button>
          <button class="icon-btn warm" data-remove="${item.id}" title="Take the whole batch out">remove</button>
        </div>
      </div>
    </li>
  `;
}

// Three ways to say what went in (Q24): a meal, a recipe, or free text for
// food that never passed through the plan. The picker searches the first two;
// free text is the fallback, not the default.
async function addDialog(root) {
  let kind = "meal";
  let picked = null; // {id, label} from the picker, or null while typing free text
  const dialog = openDialog(html`
    <h2>Put something in the freezer</h2>
    <div class="seg" data-seg>
      <button type="button" class="on" data-kind="meal">A meal</button>
      <button type="button" data-kind="recipe">A recipe</button>
      <button type="button" data-kind="text">Something else</button>
    </div>
    <div data-picker>
      <input type="search" placeholder="Search…" data-q autocomplete="off">
      <ul class="picker-list" data-results></ul>
    </div>
    <form data-f>
      <label class="field" data-text-field hidden><span>What is it</span>
        <input type="text" name="label" maxlength="300" placeholder="Mum's lasagne"></label>
      <div class="form-row">
        <label class="field"><span>Portions</span>
          <input type="number" name="portions" min="1" max="500" step="1" value="4" required></label>
        <label class="field"><span>Frozen on</span>
          <input type="date" name="frozen_on" value="${today()}"></label>
      </div>
      <label class="field"><span>Note <small>(optional)</small></span>
        <input type="text" name="note" maxlength="300" placeholder="the spicy batch"></label>
      <div class="dialog-actions">
        <button class="btn ghost" type="button" data-x>Cancel</button>
        <button class="btn" type="submit" data-submit disabled>Pick one above</button>
      </div>
    </form>
  `);
  dialog.querySelector("[data-x]").onclick = () => dialog.close();

  const picker = dialog.querySelector("[data-picker]");
  const query = dialog.querySelector("[data-q]");
  const results = dialog.querySelector("[data-results]");
  const textField = dialog.querySelector("[data-text-field]");
  const submit = dialog.querySelector("[data-submit]");
  const labelInput = dialog.querySelector('input[name="label"]');

  const refreshSubmit = () => {
    const ready = kind === "text" ? labelInput.value.trim().length > 0 : picked !== null;
    submit.disabled = !ready;
    submit.textContent = ready ? `Freeze ${picked ? picked.label : labelInput.value.trim()}` : kind === "text" ? "Name it first" : "Pick one above";
  };

  const search = async () => {
    const q = query.value.trim() || undefined;
    const rows =
      kind === "meal"
        ? (await api("/meals", { query: { search: q } })).map((m) => ({
            id: m.id,
            label: m.name,
            sub: m.recipes.map((r) => r.title).join(" + ") || "loose ingredients",
          }))
        : (await api("/recipes", { query: { search: q } })).map((r) => ({
            id: r.id,
            label: r.title,
            sub: r.servings ? `serves ${r.servings}` : "",
          }));
    render(results, html`
      ${rows.length === 0 && html`<li><span class="p-sub">Nothing matches — try “Something else” for free text.</span></li>`}
      ${rows.map(
        (row) => html`
          <li>
            <span aria-hidden="true">${foodEmoji(row.label)}</span>
            <div class="p-main">
              <div class="p-title">${row.label}</div>
              ${row.sub && html`<div class="p-sub">${row.sub}</div>`}
            </div>
            ${picked?.id === row.id
              ? html`<span class="chip green">picked</span>`
              : html`<button type="button" class="btn small" data-pick="${row.id}">Pick</button>`}
          </li>
        `,
      )}
    `);
    for (const button of results.querySelectorAll("[data-pick]")) {
      button.onclick = () => {
        picked = rows.find((r) => r.id === button.dataset.pick);
        refreshSubmit();
        search();
      };
    }
  };

  for (const button of dialog.querySelectorAll("[data-kind]")) {
    button.onclick = () => {
      kind = button.dataset.kind;
      picked = null;
      for (const other of dialog.querySelectorAll("[data-kind]")) other.classList.toggle("on", other === button);
      picker.hidden = kind === "text";
      textField.hidden = kind !== "text";
      refreshSubmit();
      if (kind === "text") labelInput.focus();
      else {
        search();
        query.focus();
      }
    };
  }
  labelInput.oninput = refreshSubmit;
  let timer;
  query.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(search, 220);
  };

  dialog.querySelector("[data-f]").onsubmit = async (event) => {
    event.preventDefault();
    const data = new FormData(event.target);
    const body = { portions: Number(data.get("portions")) || 1 };
    if (data.get("note").trim()) body.note = data.get("note").trim();
    if (data.get("frozen_on")) body.frozen_on = data.get("frozen_on");
    if (kind === "text") body.label = data.get("label").trim();
    else if (kind === "meal") body.meal_id = picked.id;
    else body.recipe_id = picked.id;
    submit.disabled = true;
    try {
      const item = await api("/freezer", { method: "POST", body });
      dialog.close();
      toast(`In it goes — ${plural(item.portions, "portion", "portions")} of ${item.label}.`, "ok");
      renderFreezer(root);
    } catch (error) {
      submit.disabled = false;
      toast(error.detail || error.message, "error");
    }
  };

  await search();
  query.focus();
}
