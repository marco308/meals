// The plan: a pool of meals you'd be happy to cook, no days attached (Q1/Q4).
// Rendered as the menu card from the marketing site, because that is what it
// is. Adding and removing meals reshapes the shopping list server-side; we
// just say so.

import { api } from "../api.js";
import { confirmDialog, emptyState, fmtRel, foodEmoji, html, openDialog, parseUtc, render, skeleton, toast } from "../dom.js";

export async function renderPlan(root, planId = null) {
  render(root, skeleton());

  let plan = null;
  if (planId) {
    plan = await api(`/plans/${planId}`);
  } else {
    try {
      plan = await api("/plans/current");
    } catch (error) {
      if (error.status !== 404) throw error;
    }
  }
  const archived = await api("/plans", { query: { status: "archived" } });

  if (!plan) {
    render(root, html`
      <div class="page narrow">
        ${emptyState("🍲", "No plan on the go", "A plan is a handful of meals you'd be happy to cook — in any order, no days attached.", html`<button class="btn" data-new-plan>Start a plan</button>`)}
        ${previousPlans(archived, null)}
      </div>
    `);
    root.querySelector("[data-new-plan]").onclick = () => newPlanDialog(root, null);
    return;
  }

  const isActive = plan.status === "active";
  const uncooked = plan.meals.filter((m) => !m.cooked_at).length;

  render(root, html`
    <div class="page narrow">
      <div class="page-head">
        <div>
          <h1>${isActive ? "The plan" : "Previous plan"}</h1>
          <p class="sub">${isActive ? "" : `wrapped up ${fmtRel(plan.archived_at)} · `}cook them in any order, or don't</p>
        </div>
        <div class="page-actions">
          ${isActive
            ? html`
                <button class="btn" data-add>＋ Add a meal</button>
                <button class="btn ghost" data-new-plan>New plan</button>
                <button class="btn ghost" data-archive>Wrap up</button>
              `
            : html`<a class="btn ghost" href="#/plan">Back to the current plan</a>`}
        </div>
      </div>

      <div class="menu-card">
        <div class="menu-title-row">
          <div>
            <div class="menu-title" data-label ${isActive ? 'title="Click to rename"' : ""}>${plan.label}</div>
            <p class="menu-sub">${plan.meals.length} ${plan.meals.length === 1 ? "option" : "options"} · no days attached</p>
          </div>
        </div>
        ${plan.meals.length === 0
          ? emptyState("🫕", "Nothing on the menu yet", "Add a few meals and the shopping list writes itself.")
          : html`<ul class="menu-list">${plan.meals.map((pm) => planMeal(pm, plan, isActive))}</ul>`}
        ${plan.meals.length > 0 && html`<p class="menu-foot">${uncooked === 0 ? "all cooked — nicely done" : `${uncooked} still to cook`}</p>`}
      </div>

      ${previousPlans(archived, plan.id)}
    </div>
  `);

  if (!isActive) return;

  root.querySelector("[data-add]").onclick = () => addMealDialog(plan, root);
  root.querySelector("[data-new-plan]").onclick = () => newPlanDialog(root, plan);
  root.querySelector("[data-archive]").onclick = async () => {
    const ok = await confirmDialog({
      title: "Wrap up this plan?",
      body: "It moves to your previous plans; meals and their cooked history stay put. The shopping list keeps its items until you finish the shop.",
      confirmLabel: "Wrap up",
    });
    if (!ok) return;
    await api(`/plans/${plan.id}/archive`, { method: "POST" });
    toast(`“${plan.label}” wrapped up.`, "ok");
    renderPlan(root);
  };

  const label = root.querySelector("[data-label]");
  label.onclick = () => renameInline(label, plan, root);

  for (const button of root.querySelectorAll("[data-cooked]")) {
    button.onclick = async () => {
      await api(`/plans/${plan.id}/meals/${button.dataset.cooked}/cooked`, { method: "POST" });
      toast("Marked cooked — it goes on the record.", "ok");
      renderPlan(root);
    };
  }
  for (const button of root.querySelectorAll("[data-remove]")) {
    button.onclick = async () => {
      await api(`/plans/${plan.id}/meals/${button.dataset.remove}`, { method: "DELETE" });
      toast("Removed — its share left the shopping list.", "ok");
      renderPlan(root);
    };
  }
}

function planMeal(pm, plan, isActive) {
  const meal = pm.meal;
  return html`
    <li class="menu-item ${pm.cooked_at ? "cooked" : ""}">
      <span class="m-emoji" aria-hidden="true">${foodEmoji(meal.name)}</span>
      <div class="m-main">
        <a class="m-name" href="#/meals/${meal.id}">${meal.name}</a>
        <div class="m-meta">
          ${meal.slot && html`<span class="chip">${meal.slot}</span>`}
          ${cookedChip(pm, meal)}
        </div>
      </div>
      ${isActive &&
      html`
        <div class="m-actions">
          ${!pm.cooked_at && html`<button class="icon-btn" data-cooked="${pm.id}" title="Mark cooked">✓ cooked</button>`}
          <button class="icon-btn warm" data-remove="${pm.id}" title="Remove from plan">remove</button>
        </div>
      `}
    </li>
  `;
}

function cookedChip(pm, meal) {
  if (pm.cooked_at) return html`<span class="chip green">cooked ${fmtRel(pm.cooked_at)}</span>`;
  if (meal.times_cooked === 0) return html`<span class="chip green">new</span>`;
  const chips = [html`<span class="chip red">cooked ${meal.times_cooked}×</span>`];
  if (meal.last_cooked_at && Date.now() - parseUtc(meal.last_cooked_at) > 28 * 86_400_000) {
    chips.push(html`<span class="chip butter">not in a while</span>`);
  }
  return chips;
}

function previousPlans(archived, currentId) {
  const others = archived.filter((p) => p.id !== currentId);
  if (others.length === 0) return "";
  return html`
    <div class="section" data-previous>
      <h2>Previous plans</h2>
      <ul class="row-list">
        ${others.slice(0, 8).map(
          (p) => html`
            <a class="row-card" href="#/plans/${p.id}">
              <span class="rc-emoji" aria-hidden="true">🗂️</span>
              <div class="rc-main">
                <div class="rc-title">${p.label}</div>
                <div class="rc-sub">${p.meal_count} ${p.meal_count === 1 ? "meal" : "meals"}</div>
              </div>
            </a>
          `,
        )}
      </ul>
    </div>
  `;
}

async function renameInline(label, plan, root) {
  const input = document.createElement("input");
  input.type = "text";
  input.value = plan.label;
  label.replaceWith(input);
  input.focus();
  input.select();
  let closed = false;
  const done = async (save) => {
    if (closed) return; // Enter triggers blur too — settle once
    closed = true;
    if (save && input.value.trim() && input.value.trim() !== plan.label) {
      await api(`/plans/${plan.id}`, { method: "PATCH", body: { label: input.value.trim() } });
    }
    renderPlan(root, plan.status === "active" ? null : plan.id);
  };
  input.onkeydown = (e) => {
    if (e.key === "Enter") done(true);
    if (e.key === "Escape") done(false);
  };
  input.onblur = () => done(true);
}

function newPlanDialog(root, currentPlan) {
  const dialog = openDialog(html`
    <h2>Start a new plan</h2>
    <form data-f>
      <label class="field"><span>Label</span>
        <input type="text" name="label" required placeholder="Next week" autofocus></label>
      ${currentPlan &&
      html`<label class="check-line">
        <input type="checkbox" name="carry"> carry over the meals from “${currentPlan.label}”
      </label>`}
      <div class="dialog-actions">
        <button class="btn ghost" type="button" data-x>Cancel</button>
        <button class="btn" type="submit">Start plan</button>
      </div>
    </form>
  `);
  dialog.querySelector("[data-x]").onclick = () => dialog.close();
  dialog.querySelector("[data-f]").onsubmit = async (event) => {
    event.preventDefault();
    const data = new FormData(event.target);
    const body = { label: data.get("label").trim() };
    if (currentPlan && data.get("carry")) body.copy_from_plan_id = currentPlan.id;
    try {
      await api("/plans", { method: "POST", body });
      dialog.close();
      toast("New plan started — the old one wrapped up itself.", "ok");
      renderPlan(root);
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };
}

async function addMealDialog(plan, root) {
  const inPlan = new Set(plan.meals.map((pm) => pm.meal.id));
  const dialog = openDialog(html`
    <h2>Add a meal</h2>
    <input type="search" placeholder="Search meals…" data-q autocomplete="off">
    <ul class="picker-list" data-results>${skeleton()}</ul>
    <p class="login-foot">or <a href="#/meals/new" data-close>make a new meal</a></p>
  `);
  dialog.querySelector("[data-close]").onclick = () => dialog.close();

  const results = dialog.querySelector("[data-results]");
  const query = dialog.querySelector("[data-q]");
  const search = async () => {
    const meals = await api("/meals", { query: { search: query.value.trim() || undefined } });
    render(results, html`
      ${meals.length === 0 && html`<li><span class="p-sub">Nothing matches — make a new meal?</span></li>`}
      ${meals.map(
        (meal) => html`
          <li>
            <span aria-hidden="true">${foodEmoji(meal.name)}</span>
            <div class="p-main">
              <div class="p-title">${meal.name}</div>
              <div class="p-sub">
                ${meal.recipes.map((r) => r.title).join(" + ") || "loose ingredients"}
                ${meal.times_cooked > 0 ? ` · cooked ${meal.times_cooked}×` : " · new"}
              </div>
            </div>
            ${inPlan.has(meal.id)
              ? html`<span class="chip green">on the menu</span>`
              : html`<button class="btn small" data-pick="${meal.id}">Add</button>`}
          </li>
        `,
      )}
    `);
    for (const button of results.querySelectorAll("[data-pick]")) {
      button.onclick = async () => {
        button.disabled = true;
        try {
          await api(`/plans/${plan.id}/meals`, { method: "POST", body: { meal_id: button.dataset.pick } });
          dialog.close();
          toast("Added — its ingredients just joined the shopping list.", "ok");
          renderPlan(root);
        } catch (error) {
          button.disabled = false;
          toast(error.detail || error.message, "error");
        }
      };
    }
  };
  let timer;
  query.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(search, 220);
  };
  await search();
  query.focus();
}
