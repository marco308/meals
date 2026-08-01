// The shopping list, in store-walking order. Every line knows why it exists
// (its sources), quantities are the server's derived truth, and checking off
// is optimistic — the strike-through should never wait for a round trip.

import { api } from "../api.js";
import { confirmDialog, emptyState, fmtDate, html, render, skeleton, toast } from "../dom.js";

let staplesMode = false;
let showExcluded = false;

export async function renderShopping(root) {
  render(root, skeleton());
  const list = await api("/shopping-list", {
    query: { include_staples: staplesMode || undefined, include_excluded: showExcluded || undefined },
  });

  // The staples check is its own screen: just the staples, nothing else,
  // matching the flow the skill teaches (include_staples → mark what's low).
  if (staplesMode) {
    renderStaplesCheck(root, list);
    return;
  }

  const ticked = list.items.filter((i) => i.checked && !i.excluded).length;
  const toGet = list.items.filter((i) => !i.excluded).length;
  const excluded = list.items.filter((i) => i.excluded);

  render(root, html`
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Shopping list</h1>
          <p class="sub">
            <span data-count>${toGet === 0 ? "nothing to get" : `${ticked} of ${toGet} in the trolley`}</span>
            ${list.hidden_staples > 0 ? ` · ${list.hidden_staples} ${list.hidden_staples === 1 ? "staple" : "staples"} waiting for a check` : ""}
          </p>
        </div>
        <div class="page-actions">
          <button class="btn ghost" data-staples>🧂 Staples check</button>
          <a class="btn ghost" href="#/list/archived">Previous shops</a>
          <button class="btn" data-finish>Finish the shop</button>
        </div>
      </div>

      <form class="quick-add" data-add>
        <input type="text" name="q" placeholder="Add something — “milk”, “2 l milk”, “500 g flour”…" autocomplete="off">
        <button class="btn" type="submit">Add</button>
      </form>

      ${list.items.length === 0
        ? emptyState("🛒", "The list writes itself", "Add meals to the plan and their ingredients appear here, merged and sorted by aisle. Or add one-offs above.")
        : html`<div class="aisle-columns">${aisleGroups(list)}</div>`}

      ${excluded.length > 0 &&
      html`
        <div class="section">
          <h2>Already have it (${excluded.length})</h2>
          <div>${excluded.map((item) => itemRow(item, true))}</div>
        </div>
      `}
      <p class="menu-foot">
        <button class="link-btn" data-show-excluded>
          ${showExcluded ? "hide the “already have it” pile" : "show the “already have it” pile"}
        </button>
      </p>
    </div>
  `);

  bind(root, list);
}

function groupByAisle(items) {
  const groups = [];
  for (const item of items) {
    const last = groups[groups.length - 1];
    if (last && last.label === item.aisle_label) last.items.push(item);
    else groups.push({ emoji: item.aisle, label: item.aisle_label, items: [item] });
  }
  return groups;
}

function aisleGroups(list) {
  return groupByAisle(list.items.filter((i) => !i.excluded)).map(
    (group) => html`
      <section class="aisle-group">
        <div class="aisle-head">
          <span class="a-emoji" aria-hidden="true">${group.emoji}</span>
          <span class="a-label">${group.label}</span>
          <span class="a-count" data-aisle-count>${group.items.filter((i) => i.checked).length}/${group.items.length}</span>
        </div>
        <div>${group.items.map((item) => itemRow(item, false))}</div>
      </section>
    `,
  );
}

function itemRow(item, inExcludedPile) {
  const adhocOnly = item.sources.every((s) => s.ad_hoc);
  return html`
    <div class="shop-item ${item.checked ? "done" : ""}" data-item="${item.id}">
      <button class="tick" data-tick="${item.id}" aria-pressed="${item.checked}" aria-label="Got it"></button>
      <span class="qty">${item.display}</span>
      <div class="i-main">
        <span class="i-name">${item.name}</span>
        ${valueBadge(item)}
        ${item.is_staple && item.staple_needed && html`<span class="chip butter">low</span>`}
        <div class="i-why">${whyLine(item)}</div>
      </div>
      <div class="i-actions">
        ${inExcludedPile
          ? html`<button class="icon-btn" data-need="${item.id}">need it after all</button>`
          : html`<button class="icon-btn" data-have="${item.id}" title="Skip this shop without forgetting why it was needed">have it</button>`}
        ${adhocOnly && html`<button class="icon-btn warm" data-del="${item.id}">delete</button>`}
      </div>
    </div>
  `;
}

function valueBadge(item) {
  if (item.value_tier === "premium") return html` <span class="value-badge" title="Worth paying up: ${item.value_note || "household verdict"}">⭐</span>`;
  if (item.value_tier === "budget") return html` <span class="value-badge" title="Own-brand is fine: ${item.value_note || "household verdict"}">💷</span>`;
  return "";
}

function whyLine(item) {
  const mealNames = [...new Set(item.sources.filter((s) => s.meal_name).map((s) => s.meal_name))];
  const adhoc = item.sources.some((s) => s.ad_hoc);
  const parts = [];
  if (mealNames.length > 0) parts.push(`for ${mealNames.slice(0, 2).join(" + ")}${mealNames.length > 2 ? ` + ${mealNames.length - 2} more` : ""}`);
  if (adhoc) parts.push(mealNames.length > 0 ? "and added by hand" : "added by hand");
  if (item.is_staple && !parts.length) parts.push("staple");
  return parts.join(" ");
}

function bind(root, list) {
  root.querySelector("[data-staples]").onclick = () => {
    staplesMode = !staplesMode;
    renderShopping(root);
  };
  root.querySelector("[data-show-excluded]").onclick = () => {
    showExcluded = !showExcluded;
    renderShopping(root);
  };

  root.querySelector("[data-finish]").onclick = async () => {
    const ok = await confirmDialog({
      title: "Finish the shop?",
      body: "This list is archived for the record and a fresh, empty one starts — adding meals to the plan fills it again.",
      confirmLabel: "Finish",
    });
    if (!ok) return;
    await api("/shopping-list/archive", { method: "POST" });
    toast("Shop finished — fresh list started.", "ok");
    renderShopping(root);
  };

  const addForm = root.querySelector("[data-add]");
  addForm.onsubmit = async (event) => {
    event.preventDefault();
    const text = addForm.q.value.trim();
    if (!text) return;
    try {
      await api("/shopping-list/items", {
        method: "POST",
        body: { ...parseQuick(text), id: crypto.randomUUID() },
      });
      addForm.q.value = "";
      renderShopping(root);
    } catch (error) {
      toast(error.detail || error.message, "error"); // e.g. the metric-units guidance
    }
  };

  // Optimistic tick: strike immediately, roll back if the server disagrees.
  for (const tick of root.querySelectorAll("[data-tick]")) {
    tick.onclick = async () => {
      const row = tick.closest("[data-item]");
      const was = tick.getAttribute("aria-pressed") === "true";
      tick.setAttribute("aria-pressed", String(!was));
      row.classList.toggle("done", !was);
      updateCounts(root);
      try {
        await api(`/shopping-list/items/${tick.dataset.tick}`, { method: "PATCH", body: { checked: !was } });
      } catch (error) {
        tick.setAttribute("aria-pressed", String(was));
        row.classList.toggle("done", was);
        updateCounts(root);
        toast(error.detail || error.message, "error");
      }
    };
  }
  for (const button of root.querySelectorAll("[data-have]")) {
    button.onclick = async () => {
      await api(`/shopping-list/items/${button.dataset.have}`, { method: "PATCH", body: { excluded: true } });
      renderShopping(root);
    };
  }
  for (const button of root.querySelectorAll("[data-need]")) {
    button.onclick = async () => {
      await api(`/shopping-list/items/${button.dataset.need}`, { method: "PATCH", body: { excluded: false } });
      renderShopping(root);
    };
  }
  for (const button of root.querySelectorAll("[data-del]")) {
    button.onclick = async () => {
      try {
        await api(`/shopping-list/items/${button.dataset.del}`, { method: "DELETE" });
        renderShopping(root);
      } catch (error) {
        toast(error.detail || error.message, "error"); // the 409 names the meals that need it
      }
    };
  }
}

// The staples check: only the staples, run down like a pantry list. A staple
// marked low joins the main list in its aisle (staple_needed); the rest stay
// hidden from the shop. Staples appear here once something has ever put them
// on the list — that's the server's model, and the skill's.
function renderStaplesCheck(root, list) {
  const staples = list.items.filter((i) => i.is_staple);
  const low = staples.filter((i) => i.staple_needed).length;

  render(root, html`
    <div class="page narrow">
      <div class="page-head">
        <div>
          <h1>Staples check</h1>
          <p class="sub">${staples.length === 0 ? "nothing is marked as a staple yet" : `${staples.length} ${staples.length === 1 ? "staple" : "staples"} · ${low} on the list`}</p>
        </div>
        <div class="page-actions">
          <button class="btn leek" data-staples>Done — back to the list</button>
        </div>
      </div>

      <div class="staples-banner">
        <b>Pre-shop pantry sweep.</b> Staples live at home, so they stay off the
        list until you say you're low — anything you mark joins the shop in its
        aisle, the rest stay hidden.
      </div>

      ${staples.length === 0
        ? emptyState("🧂", "No staples to check", "Mark things like olive oil and salt as staples on the Ingredients page. Once a recipe or a quick-add has ever put one on a list, it shows up here before each shop.")
        : groupByAisle(staples).map(
            (group) => html`
              <section class="aisle-group">
                <div class="aisle-head">
                  <span class="a-emoji" aria-hidden="true">${group.emoji}</span>
                  <span class="a-label">${group.label}</span>
                  <span class="a-count">${group.items.filter((i) => i.staple_needed).length}/${group.items.length} low</span>
                </div>
                <div>${group.items.map((item) => stapleRow(item))}</div>
              </section>
            `,
          )}
    </div>
  `);

  root.querySelector("[data-staples]").onclick = () => {
    staplesMode = false;
    renderShopping(root);
  };
  for (const button of root.querySelectorAll("[data-low]")) {
    button.onclick = async () => {
      const needed = button.dataset.needed === "true";
      try {
        await api(`/shopping-list/items/${button.dataset.low}`, { method: "PATCH", body: { staple_needed: !needed } });
        if (!needed) toast("On the list, in its aisle.", "ok");
        renderShopping(root);
      } catch (error) {
        toast(error.detail || error.message, "error");
      }
    };
  }
}

function stapleRow(item) {
  return html`
    <div class="shop-item">
      <span class="qty">${item.display}</span>
      <div class="i-main">
        <span class="i-name">${item.name}</span>${valueBadge(item)}
      </div>
      ${item.staple_needed
        ? html`
            <span class="chip butter">on the list</span>
            <button class="icon-btn" data-low="${item.id}" data-needed="true">stocked after all</button>
          `
        : html`<button class="btn small" data-low="${item.id}" data-needed="false">Low — add it</button>`}
    </div>
  `;
}

// Keep the header and per-aisle tallies honest during optimistic ticking,
// without a full re-render that would cut the strike animation short.
function updateCounts(root) {
  for (const group of root.querySelectorAll(".aisle-group")) {
    const rows = group.querySelectorAll(".shop-item");
    const done = group.querySelectorAll(".shop-item.done").length;
    const count = group.querySelector("[data-aisle-count]");
    if (count) count.textContent = `${done}/${rows.length}`;
  }
  const rows = root.querySelectorAll(".aisle-columns .shop-item");
  const done = root.querySelectorAll(".aisle-columns .shop-item.done").length;
  const label = root.querySelector("[data-count]");
  if (label) label.textContent = rows.length === 0 ? "nothing to get" : `${done} of ${rows.length} in the trolley`;
}

// "2 l milk" / "500g flour" / "2 tins of chopped tomatoes" / plain "milk".
// The server enforces the unit convention (metric or a natural count) and its
// 422 explains the conversion, so this parser can stay naive.
function parseQuick(text) {
  const match = text.match(/^(\d+(?:[.,]\d+)?)\s*([a-zA-Z]+)?\s+(.+)$/);
  if (!match) return { name: text };
  let name = match[3].trim();
  if (name.toLowerCase().startsWith("of ")) name = name.slice(3);
  return {
    name,
    quantity: parseFloat(match[1].replace(",", ".")),
    unit: match[2] || null,
    raw: text,
  };
}

export async function renderShoppingArchive(root) {
  render(root, skeleton());
  const lists = await api("/shopping-list/archived");
  render(root, html`
    <div class="page narrow">
      <div class="crumb"><a href="#/list">← Shopping list</a></div>
      <div class="page-head">
        <div>
          <h1>Previous shops</h1>
          <p class="sub">what a finished list looked like, for the record</p>
        </div>
      </div>
      ${lists.length === 0
        ? emptyState("🧾", "No previous shops yet", "Finish a shop and it lands here.")
        : html`
            <div class="card">
              <table class="plain">
                <thead><tr><th>Shopped</th><th>Started</th><th>Items</th></tr></thead>
                <tbody>
                  ${lists.map(
                    (l) => html`
                      <tr>
                        <td>${fmtDate(l.archived_at)}</td>
                        <td>${fmtDate(l.created_at)}</td>
                        <td>${l.item_count}</td>
                      </tr>
                    `,
                  )}
                </tbody>
              </table>
            </div>
          `}
    </div>
  `);
}
