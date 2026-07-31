// Rendering helpers. No framework: views build HTML with the `html` tagged
// template, which escapes every interpolated value unless it is itself a
// template result (or wrapped in raw()). That one rule is the XSS boundary —
// recipe titles, meal names and error details all pass through here.

class Raw {
  constructor(value) {
    this.value = value;
  }
}

export const esc = (value) =>
  String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const part = (value) => {
  if (value === null || value === undefined || value === false) return "";
  if (value instanceof Raw) return value.value;
  if (Array.isArray(value)) return value.map(part).join("");
  return esc(value);
};

export function html(strings, ...values) {
  let out = strings[0];
  for (let i = 0; i < values.length; i++) out += part(values[i]) + strings[i + 1];
  return new Raw(out);
}

export const raw = (value) => new Raw(String(value));

export function render(root, template) {
  root.innerHTML = template instanceof Raw ? template.value : String(template);
}

// ── small utilities ──────────────────────────────────────────────────

export function debounce(fn, ms = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

// The API serialises datetimes as naive UTC ("2026-07-31T18:38:52") — tell
// the Date parser so, but leave anything already carrying an offset alone.
export const parseUtc = (iso) => new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");

const REL = new Intl.RelativeTimeFormat("en-GB", { numeric: "auto" });

export function fmtRel(iso) {
  if (!iso) return "";
  const days = Math.round((parseUtc(iso) - Date.now()) / 86_400_000);
  if (Math.abs(days) >= 365) return REL.format(Math.trunc(days / 365), "year");
  if (Math.abs(days) >= 30) return REL.format(Math.trunc(days / 30), "month");
  if (Math.abs(days) >= 7) return REL.format(Math.trunc(days / 7), "week");
  if (days !== 0) return REL.format(days, "day");
  return "today";
}

export function fmtDate(iso) {
  if (!iso) return "";
  return parseUtc(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

// A stable food emoji per name, so the plan reads like the menu card on the
// marketing site without anyone having to pick emoji by hand. An explicit
// array: spreading a string would split multi-codepoint emoji (🌶️, 🍽️).
const FOOD = ["🍲", "🍛", "🥧", "🍅", "🌯", "🥘", "🍝", "🍜", "🍳", "🥗", "🌮", "🍕", "🐟", "🍗", "🥬", "🍆", "🫕", "🥟", "🍚", "🫘", "🌶️", "🧀", "🥕", "🍄"];
export function foodEmoji(name) {
  let hash = 0;
  for (const ch of String(name)) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return FOOD[hash % FOOD.length];
}

// ── toast ────────────────────────────────────────────────────────────

export function toast(message, kind = "") {
  const rack = document.getElementById("toast-rack");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  rack.append(el);
  setTimeout(() => {
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 350);
  }, kind === "error" ? 7000 : 4000);
}

// ── dialogs ──────────────────────────────────────────────────────────

export function openDialog(template) {
  const dialog = document.createElement("dialog");
  render(dialog, template);
  document.body.append(dialog);
  // Nothing here may depend on the dialog 'close' *event* — some embedded
  // browsers never deliver it (found the hard way: confirms hung forever).
  // close() is patched to also remove the element, and Escape (the 'cancel'
  // event) is routed through the same path.
  const nativeClose = dialog.close.bind(dialog);
  dialog.close = (value) => {
    nativeClose(value);
    dialog.remove();
  };
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    dialog.close();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close(); // click on the backdrop
  });
  dialog.showModal();
  return dialog;
}

export function confirmDialog({ title, body, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    const dialog = openDialog(html`
      <h2>${title}</h2>
      <p>${body}</p>
      <div class="dialog-actions">
        <button class="btn ghost" data-x="cancel">Cancel</button>
        <button class="btn ${danger ? "danger" : ""}" data-x="ok">${confirmLabel}</button>
      </div>
    `);
    const settle = (value) => {
      resolve(value);
      dialog.close();
    };
    dialog.querySelector('[data-x="ok"]').onclick = () => settle(true);
    dialog.querySelector('[data-x="cancel"]').onclick = () => settle(false);
    dialog.addEventListener("cancel", () => resolve(false)); // Escape
  });
}

export const skeleton = () => html`
  <div class="skeleton"><div class="bone"></div><div class="bone"></div><div class="bone"></div></div>
`;

export const emptyState = (emoji, title, body, actions = "") => html`
  <div class="empty">
    <span class="e-emoji">${emoji}</span>
    <h2>${title}</h2>
    <p>${body}</p>
    ${actions}
  </div>
`;
