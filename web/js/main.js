// Shell and router. Hash routing, so the server only ever serves /app/ and
// its assets — deep links (#/recipes/…) never need server-side routes.

import { api, session } from "./api.js";
import { html, render, toast } from "./dom.js";
import { renderIngredients } from "./views/ingredients.js";
import { renderLogin } from "./views/login.js";
import { renderMealDetail, renderMealEditor, renderMeals } from "./views/meals.js";
import { renderPlan } from "./views/plan.js";
import { renderRecipeDetail, renderRecipeEditor, renderRecipes } from "./views/recipes.js";
import { renderSettings } from "./views/settings.js";
import { renderShopping, renderShoppingArchive } from "./views/shopping.js";

const NAV = [
  { hash: "#/plan", emoji: "🍲", label: "Plan", match: /^#\/plans?/ },
  { hash: "#/list", emoji: "🛒", label: "Shopping list", match: /^#\/list/ },
  { hash: "#/recipes", emoji: "📖", label: "Recipes", match: /^#\/recipes/ },
  { hash: "#/meals", emoji: "🍽️", label: "Meals", match: /^#\/meals/ },
  { hash: "#/ingredients", emoji: "🥕", label: "Ingredients", match: /^#\/ingredients/ },
  { hash: "#/settings", emoji: "⚙️", label: "Settings", match: /^#\/settings/ },
];

const ROUTES = [
  [/^#\/plan$/, (root) => renderPlan(root), "Plan"],
  [/^#\/plans\/([0-9a-f-]+)$/, (root, m) => renderPlan(root, m[1]), "Plan"],
  [/^#\/list$/, (root) => renderShopping(root), "Shopping list"],
  [/^#\/list\/archived$/, (root) => renderShoppingArchive(root), "Previous shops"],
  [/^#\/recipes$/, (root) => renderRecipes(root), "Recipes"],
  [/^#\/recipes\/new$/, (root) => renderRecipeEditor(root, null), "New recipe"],
  [/^#\/recipes\/([0-9a-f-]+)$/, (root, m) => renderRecipeDetail(root, m[1]), "Recipe"],
  [/^#\/recipes\/([0-9a-f-]+)\/edit$/, (root, m) => renderRecipeEditor(root, m[1]), "Edit recipe"],
  [/^#\/meals$/, (root) => renderMeals(root), "Meals"],
  [/^#\/meals\/new$/, (root) => renderMealEditor(root, null), "New meal"],
  [/^#\/meals\/([0-9a-f-]+)$/, (root, m) => renderMealDetail(root, m[1]), "Meal"],
  [/^#\/meals\/([0-9a-f-]+)\/edit$/, (root, m) => renderMealEditor(root, m[1]), "Edit meal"],
  [/^#\/ingredients$/, (root) => renderIngredients(root), "Ingredients"],
  [/^#\/settings$/, (root) => renderSettings(root), "Settings"],
];

const shell = document.getElementById("shell");
let contentRoot = null;

function renderShell() {
  const user = session.user;
  render(shell, html`
    <div class="app">
      <aside class="side">
        <a class="wordmark" href="#/plan">yamp<span class="dot">.</span></a>
        <nav aria-label="Sections">
          ${NAV.map(
            (item) => html`
              <a class="nav-link" data-nav href="${item.hash}">
                <span class="n-emoji" aria-hidden="true">${item.emoji}</span>
                <span class="n-label">${item.label}</span>
              </a>
            `,
          )}
        </nav>
        <div class="spacer"></div>
        <div class="side-user">
          <b>${user?.household_name || "Home"}</b>
          ${user?.display_name || ""} · <button class="link-btn" data-signout>sign out</button>
        </div>
      </aside>
      <main class="content"><div id="view"></div></main>
    </div>
  `);
  shell.querySelector("[data-signout]").onclick = () => {
    session.clear();
    window.location.hash = "#/login";
  };
  contentRoot = shell.querySelector("#view");
}

function markActive(hash) {
  for (const link of shell.querySelectorAll("[data-nav]")) {
    const item = NAV.find((n) => n.hash === link.getAttribute("href"));
    link.classList.toggle("active", item.match.test(hash));
  }
}

async function route() {
  const hash = window.location.hash || "#/plan";

  if (!session.token) {
    contentRoot = null;
    if (hash !== "#/login") {
      window.location.hash = "#/login";
      return;
    }
    renderLogin(shell);
    document.title = "YAMP";
    return;
  }
  if (hash === "#/login") {
    window.location.hash = "#/plan";
    return;
  }

  if (!contentRoot) renderShell();
  markActive(hash);

  for (const [pattern, handler, title] of ROUTES) {
    const match = hash.match(pattern);
    if (match) {
      document.title = `${title} · YAMP`;
      try {
        await handler(contentRoot, match);
      } catch (error) {
        toast(error.detail || error.message, "error");
      }
      return;
    }
  }
  window.location.hash = "#/plan";
}

window.addEventListener("hashchange", route);

// "/" focuses the search box on list views, like every good big-screen tool.
window.addEventListener("keydown", (event) => {
  if (event.key !== "/" || event.target.matches("input, textarea, select")) return;
  const search = document.querySelector("input[type=search]");
  if (search) {
    event.preventDefault();
    search.focus();
  }
});

// Boot: trust the cached session for instant paint, then verify it (and
// refresh the cached user/household names) in the background — api() drops
// us back at #/login on a 401.
route();
if (session.token) {
  api("/auth/me")
    .then((user) => {
      session.saveUser(user);
      const badge = document.querySelector(".side-user");
      if (badge) {
        render(badge, html`
          <b>${user.household_name || "Home"}</b>
          ${user.display_name} · <button class="link-btn" data-signout>sign out</button>
        `);
        badge.querySelector("[data-signout]").onclick = () => {
          session.clear();
          window.location.hash = "#/login";
        };
      }
    })
    .catch(() => {}); // a 401 already routed to login; network errors keep the cached shell
}
