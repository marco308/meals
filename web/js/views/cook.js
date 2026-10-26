// Cook mode (Q29): one recipe, one step at a time, for following at the hob.
// The step being cooked is big, the ingredients it uses are pinned beside it
// with their amounts, and the rest of the ingredients sit dimmed below, ticked
// once an earlier step has used them. Nothing re-sorts, so nothing jumps
// about while somebody is reading it.
//
// Steps come from the server: matched by name, or written by the household's
// assistant with per-step amounts. Tap a step (or press space) to hear it.

import { api } from "../api.js";
import { html, parseUtc, render, skeleton, toast } from "../dom.js";
import * as speech from "../speech.js";

// How long after asking the assistant this page keeps checking for its
// answer, and how often.
const WAIT_FOR_ASSISTANT_MS = 3 * 60_000;
const CHECK_EVERY_MS = 10_000;

let state = null;

export async function renderCookMode(root, recipeId) {
  render(root, skeleton());
  const [recipe, helper] = await Promise.all([
    api(`/recipes/${recipeId}`),
    api("/household/assistant").catch(() => null),
  ]);
  teardown();
  state = { root, recipe, helper, current: 0, autoRead: readAutoRead(), wakeLock: null, poll: null };
  draw();
  keepAwake();
  document.addEventListener("keydown", onKey);
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("hashchange", teardown, { once: true });
  speech.onVoices(() => state && drawVoicePicker());
  if (recentlyAsked(recipe)) waitForAssistant();
}

function teardown() {
  if (!state) return;
  speech.stop();
  clearInterval(state.poll);
  state.wakeLock?.release?.().catch(() => {});
  document.removeEventListener("keydown", onKey);
  document.removeEventListener("visibilitychange", onVisible);
  state = null;
}

// ── drawing ─────────────────────────────────────────────────────────

function draw() {
  const { root, recipe } = state;
  const steps = recipe.steps;
  if (!steps.length) {
    render(root, html`
      <div class="page narrow">
        <div class="page-head"><div><a class="sub" href="#/recipes/${recipe.id}">← ${recipe.title}</a>
          <h1>Nothing to cook along to</h1></div></div>
        <div class="card"><p>This recipe has no method written down, so there are no steps.
          ${recipe.source_url ? html`The <a href="${recipe.source_url}" target="_blank" rel="noopener noreferrer">original page</a> has it, and` : "You can"}
          add it with <a href="#/recipes/${recipe.id}/edit">Edit</a>.</p></div>
      </div>
    `);
    return;
  }
  const step = steps[state.current];
  render(root, html`
    <div class="page cook">
      <div class="page-head">
        <div>
          <a class="sub" href="#/recipes/${recipe.id}">← ${recipe.title}</a>
          <h1>Step ${step.number} <span class="of">of ${steps.length}</span></h1>
        </div>
        <div class="page-actions cook-voice" data-voice></div>
      </div>

      <div class="cook-grid">
        <div class="cook-main">
          <section class="card cook-now" aria-live="polite">
            ${step.section && html`<p class="cook-section">${step.section}</p>`}
            <p class="cook-text">${step.text}</p>
            <div class="cook-nav">
              <button class="btn ghost" data-prev ${state.current === 0 ? "disabled" : ""}>← Back</button>
              ${speech.speechAvailable()
                ? html`<button class="btn leek" data-speak>🔊 Read it out</button>`
                : ""}
              <button class="btn" data-next ${state.current === steps.length - 1 ? "disabled" : ""}>Next →</button>
            </div>
            <p class="sub cook-keys">← → to move · space to hear it · tap any step below</p>
          </section>
          <ol class="card cook-steps">
            ${steps.map(
              (s, index) => html`
                <li class="${index === state.current ? "now" : index < state.current ? "done" : ""}">
                  <button class="cook-step" data-step="${index}">
                    <span class="num">${s.number}</span>
                    <span>${s.section && s.section !== steps[index - 1]?.section ? html`<b>${s.section}</b> · ` : ""}${s.text}</span>
                  </button>
                </li>
              `,
            )}
          </ol>
        </div>

        <aside class="cook-side">
          <div class="card cook-pinned">
            <h2>For this step</h2>
            ${step.ingredients.length
              ? html`<ul class="cook-ings">${step.ingredients.map(
                  (ing) => html`<li><span class="amt">${ing.amount || ""}</span><span>${ing.name}</span></li>`,
                )}</ul>`
              : html`<p class="sub">Nothing new from the ingredients list.</p>`}
          </div>
          <div class="card cook-rest">
            <h2>Everything else</h2>
            <ul class="cook-ings dim">${otherIngredients(step).map(
              (line) => html`<li class="${line.used ? "used" : ""}"><span class="amt">${line.display || ""}</span>
                <span>${line.name}${line.used ? " ✓" : ""}</span></li>`,
            )}</ul>
          </div>
          ${assistantCard()}
        </aside>
      </div>

    </div>
  `);
  drawVoicePicker();
  bind();
  // A step picked from the list below brings the big card back into view.
  const now = root.querySelector(".cook-now");
  if (now && now.getBoundingClientRect().top < 0) now.scrollIntoView({ block: "start", behavior: "smooth" });
}

// The recipe's other lines, with "used" for those an earlier step took.
function otherIngredients(step) {
  const here = new Set(step.ingredients.map((ing) => ing.ingredient_id));
  const used = new Set(
    state.recipe.steps.slice(0, state.current).flatMap((s) => s.ingredients.map((ing) => ing.ingredient_id)),
  );
  const seen = new Set();
  const rows = [];
  for (const line of state.recipe.ingredients) {
    if (here.has(line.ingredient_id) || seen.has(line.ingredient_id)) continue;
    seen.add(line.ingredient_id);
    rows.push({ ...line, used: used.has(line.ingredient_id) });
  }
  return rows;
}

function recentlyAsked(recipe) {
  if (!recipe.steps_requested_at || recipe.steps_source === "ai") return false;
  return Date.now() - parseUtc(recipe.steps_requested_at) < WAIT_FOR_ASSISTANT_MS;
}

function assistantCard() {
  const { recipe, helper } = state;
  if (recipe.steps_source === "ai") {
    return html`<p class="sub cook-source">✨ Steps written by your assistant.</p>`;
  }
  const asked = recentlyAsked(recipe);
  return html`
    <div class="card cook-assistant">
      <p class="sub">
        ${recipe.steps_outdated
          ? "The recipe has changed since your assistant wrote its steps, so these are matched by name for now."
          : "These steps were matched by name, so amounts that get split between steps show in full on the first one."}
        ${asked ? html` <b>Asked your assistant; this page updates when it answers.</b>` : ""}
      </p>
      <div class="dialog-actions">
        <button class="btn ghost small" data-copy-prompt>Copy prompt</button>
        ${helper?.configured
          ? html`<button class="btn small" data-ask ${asked ? "disabled" : ""}>✨ Ask my assistant</button>`
          : html`<a class="btn ghost small" href="#/settings">Set up an assistant</a>`}
      </div>
    </div>
  `;
}

function drawVoicePicker() {
  const slot = state?.root.querySelector("[data-voice]");
  if (!slot) return;
  if (!speech.speechAvailable()) {
    render(slot, "");
    return;
  }
  const voices = speech.rankedVoices();
  const chosen = speech.chosenVoice();
  render(slot, html`
    <label class="chip click ${state.autoRead ? "on" : ""}"><input type="checkbox" data-auto ${state.autoRead ? "checked" : ""} hidden>
      read each step</label>
    ${voices.length > 1
      ? html`<select data-pick-voice aria-label="Voice">${voices.map(
          (voice) =>
            html`<option value="${voice.voiceURI}" ${voice === chosen ? "selected" : ""}>${voice.name} (${voice.lang})</option>`,
        )}</select>`
      : ""}
    <select data-rate aria-label="Speed">
      ${[0.8, 0.9, 1, 1.1, 1.2].map(
        (value) => html`<option value="${value}" ${speech.rate() === value ? "selected" : ""}>${value === 1 ? "normal" : `${value}×`}</option>`,
      )}
    </select>
  `);
  slot.querySelector("[data-auto]").onchange = (event) => {
    state.autoRead = event.target.checked;
    try {
      localStorage.setItem("yamp.cook.autoread", state.autoRead ? "1" : "0");
    } catch {}
    drawVoicePicker();
    if (state.autoRead) say();
  };
  slot.querySelector("[data-pick-voice]")?.addEventListener("change", (event) => {
    speech.chooseVoice(event.target.value);
    say();
  });
  slot.querySelector("[data-rate]").onchange = (event) => {
    speech.setRate(Number(event.target.value));
    say();
  };
}

function readAutoRead() {
  try {
    return localStorage.getItem("yamp.cook.autoread") === "1";
  } catch {
    return false;
  }
}

// ── behaviour ───────────────────────────────────────────────────────

function bind() {
  const { root } = state;
  root.querySelector("[data-prev]")?.addEventListener("click", () => go(state.current - 1));
  root.querySelector("[data-next]")?.addEventListener("click", () => go(state.current + 1));
  root.querySelector("[data-speak]")?.addEventListener("click", () => toggleSpeech());
  for (const button of root.querySelectorAll("[data-step]")) {
    button.onclick = () => go(Number(button.dataset.step), { speak: true });
  }
  root.querySelector("[data-copy-prompt]")?.addEventListener("click", copyPrompt);
  root.querySelector("[data-ask]")?.addEventListener("click", ask);
}

function go(index, { speak = false } = {}) {
  const steps = state.recipe.steps;
  if (index < 0 || index >= steps.length) return;
  const moved = index !== state.current;
  state.current = index;
  speech.stop();
  draw();
  if (speak || (moved && state.autoRead)) say();
}

function say() {
  const step = state?.recipe.steps[state.current];
  if (step) speech.speak(step.spoken || step.text);
}

function toggleSpeech() {
  if (speech.speaking()) speech.stop();
  else say();
}

function onKey(event) {
  if (!state || event.target.closest?.("input, select, textarea, dialog")) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === "ArrowRight") go(state.current + 1);
  else if (event.key === "ArrowLeft") go(state.current - 1);
  else if (event.key === " ") toggleSpeech();
  else if (event.key === "Escape") window.location.hash = `#/recipes/${state.recipe.id}`;
  else return;
  event.preventDefault();
}

// The screen stays on while cooking: hands are floury and the phone is
// propped against the kettle. A wake lock is dropped whenever the page is
// hidden, so it is asked for again on the way back.
async function keepAwake() {
  try {
    if (state && "wakeLock" in navigator) state.wakeLock = await navigator.wakeLock.request("screen");
  } catch {
    // Refused (battery saver, an older browser): cook mode works without it.
  }
}

function onVisible() {
  if (document.visibilityState === "visible") keepAwake();
  else speech.stop();
}

// ── the assistant ───────────────────────────────────────────────────

function promptText(recipe) {
  return (
    `Please prepare cook mode for my recipe "${recipe.title}" (id ${recipe.id}) in YAMP. ` +
    "Use get_cook_steps to read it, then annotate_recipe_steps to write its method back as steps, " +
    "each with the ingredients it uses and how much of each that step needs " +
    `(or GET and PUT ${new URL(`../recipes/${recipe.id}/steps`, window.location.href).href} over the API).`
  );
}

async function copyPrompt() {
  try {
    await navigator.clipboard.writeText(promptText(state.recipe));
    toast("Copied. Paste it into your assistant.", "ok");
  } catch {
    toast("Couldn't reach the clipboard from here; the prompt is in the box below.", "error");
    const box = document.createElement("textarea");
    box.className = "cook-prompt";
    box.value = promptText(state.recipe);
    state.root.querySelector(".cook-assistant")?.append(box);
    box.select();
  }
}

async function ask() {
  try {
    state.recipe = await api(`/recipes/${state.recipe.id}/steps/ask`, { method: "POST" });
    toast("Asked your assistant. This page updates when it answers.", "ok");
    draw();
    waitForAssistant();
  } catch (error) {
    toast(error.detail || error.message, "error");
  }
}

function waitForAssistant() {
  clearInterval(state.poll);
  const started = Date.now();
  const id = state.recipe.id;
  state.poll = setInterval(async () => {
    if (!state || state.recipe.id !== id || Date.now() - started > WAIT_FOR_ASSISTANT_MS) {
      clearInterval(state?.poll);
      if (state) draw();
      return;
    }
    try {
      const fresh = await api(`/recipes/${id}`);
      if (!state || fresh.steps_source !== "ai") return;
      clearInterval(state.poll);
      state.recipe = fresh;
      state.current = Math.min(state.current, fresh.steps.length - 1);
      toast("✨ Your assistant has written the steps.", "ok");
      draw();
    } catch {
      // A blip while waiting is not worth a toast; the next check tries again.
    }
  }, CHECK_EVERY_MS);
}
