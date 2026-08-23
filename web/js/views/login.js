// Sign in / create a household / join with an invite / reset a password.
// The register modes mirror decision Q19: no invite code → a brand-new
// household; with one → join whoever minted it. A server with
// REGISTRATION_ENABLED=false answers 403 with an explanation we just show.

import { api, session } from "../api.js";
import { html, render, toast } from "../dom.js";

let mode = "signin"; // signin | register | reset
let joinMode = "new"; // new | invite
let resetStage = "request"; // request | confirm

export function renderLogin(root) {
  render(root, html`
    <div class="login-wrap">
      <div class="login-card">
        <span class="wordmark">yamp<span class="dot">.</span></span>
        <p class="strap">the big-screen half of your meal planner</p>
        <div class="login-tabs" role="tablist">
          <button data-mode="signin" class="${mode === "signin" ? "on" : ""}">Sign in</button>
          <button data-mode="register" class="${mode === "register" ? "on" : ""}">Register</button>
        </div>
        <form data-form>${formBody()}</form>
        <div data-allowances></div>
        <p class="login-foot" data-foot></p>
      </div>
    </div>
  `);

  for (const tab of root.querySelectorAll("[data-mode]")) {
    tab.onclick = () => {
      mode = tab.dataset.mode;
      resetStage = "request";
      renderLogin(root);
    };
  }
  for (const seg of root.querySelectorAll("[data-join]")) {
    seg.onclick = () => {
      joinMode = seg.dataset.join;
      renderLogin(root);
    };
  }

  paintFoot(root);
  paintAllowances(root);
  loadConfig(root);

  const form = root.querySelector("[data-form]");
  form.onsubmit = async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await submit(data, root);
    } catch (error) {
      toast(error.detail || error.message, "error");
    } finally {
      button.disabled = false;
    }
  };
}

// ── what this server can do, and what an account here includes ───────────
//
// planning/08-freemium.md §4: the numbers ride on the unauthenticated
// GET /client-config precisely so this page can show them, since a signup page
// has nobody to log in as yet. On a server that limits nothing every one of
// them is null and this says nothing at all, which is §1 doing its job.
//
// The same answer says whether the server can send email at all, which is what
// decides the reset link below: a deployment with no SMTP answers 503 to
// POST /auth/password-reset, so offering it regardless points people at a door
// they can do nothing about (issue #49 fixed exactly this in the iOS app).
//
// It lands after the card is on screen, so both readers paint their own slot
// rather than re-rendering: the form may be half typed by then.
//
// Which is also why the fetch settles into `config` whether it succeeded or
// not. The link is withheld until the answer is in, so a rejection that left
// it null would hide a working feature for good rather than for a moment.

const ALLOWANCES = [
  // The order is the order the wall is met in: the member limit is the gate.
  ["members", (n) => `${n} ${n === 1 ? "person" : "people"}`],
  ["recipes", (n) => `${n} recipes`],
  ["meals", (n) => `${n} meals`],
  ["plans", (n) => `${n} plans on the go`],
  ["ingredients", (n) => `${n} ingredients`],
  ["supermarkets", (n) => `${n} ${n === 1 ? "supermarket" : "supermarkets"}`],
  ["api_tokens", (n) => `${n} API ${n === 1 ? "token" : "tokens"}`],
  ["ingests_per_month", (n) => `${n} recipes read from a URL each month`],
];

let config = null; // the answer, or {} if it could not be had; null until it settles
let asked = false; // a re-render mid-flight must not ask a second time

function loadConfig(root) {
  if (asked) return;
  asked = true;
  api("/client-config")
    .then((answer) => {
      config = answer || {};
    })
    .catch(() => {
      config = {}; // a server that cannot answer this has a louder problem
    })
    .finally(() => {
      paintFoot(root);
      paintAllowances(root);
    });
}

function paintAllowances(root) {
  const slot = root.querySelector("[data-allowances]");
  if (!slot || mode !== "register" || joinMode !== "new") return;
  // Shown only when starting a household: an invite code joins somebody else's,
  // whose allowances are theirs and not these.
  const allowances = config?.free_tier_limits || {};
  const rows = ALLOWANCES.filter(([name]) => allowances[name] !== null && allowances[name] !== undefined);
  if (rows.length === 0) return;
  render(slot, html`
    <div class="allow-note">
      <b>What an account here includes</b>
      <ul>${rows.map(([name, phrase]) => html`<li>${phrase(allowances[name])}</li>`)}</ul>
    </div>
  `);
}

// The foot's buttons are bound here rather than in renderLogin because a
// repaint replaces them, and the reset link only learns whether it belongs
// once /client-config has answered.
function paintFoot(root) {
  const slot = root.querySelector("[data-foot]");
  if (!slot) return;
  render(slot, footNote());
  const forgot = slot.querySelector("[data-forgot]");
  if (forgot) {
    forgot.onclick = () => {
      mode = "reset";
      resetStage = "request";
      renderLogin(root);
    };
  }
  const back = slot.querySelector("[data-back]");
  if (back) {
    back.onclick = () => {
      mode = "signin";
      renderLogin(root);
    };
  }
}

function formBody() {
  if (mode === "signin") {
    return html`
      <label class="field"><span>Email</span>
        <input type="email" name="email" required autocomplete="email" autofocus></label>
      <label class="field"><span>Password</span>
        <input type="password" name="password" required autocomplete="current-password"></label>
      <button class="btn" type="submit">Sign in</button>
    `;
  }
  if (mode === "register") {
    return html`
      <div class="seg">
        <button type="button" data-join="new" class="${joinMode === "new" ? "on" : ""}">Start a household</button>
        <button type="button" data-join="invite" class="${joinMode === "invite" ? "on" : ""}">Join with an invite</button>
      </div>
      <label class="field"><span>Your name</span>
        <input type="text" name="display_name" required autocomplete="name" placeholder="Marcus"></label>
      <label class="field"><span>Email</span>
        <input type="email" name="email" required autocomplete="email"></label>
      <label class="field"><span>Password</span>
        <input type="password" name="password" required minlength="8" autocomplete="new-password"></label>
      ${joinMode === "new"
        ? html`<label class="field"><span>Household name (optional)</span>
            <input type="text" name="household_name" placeholder="Home"></label>`
        : html`<label class="field"><span>Invite code</span>
            <input type="text" name="invite_code" required autocomplete="off"
                   placeholder="from whoever runs your household"></label>`}
      <button class="btn" type="submit">${joinMode === "new" ? "Create household" : "Join household"}</button>
    `;
  }
  // reset
  if (resetStage === "request") {
    return html`
      <label class="field"><span>Email</span>
        <input type="email" name="email" required autocomplete="email" autofocus></label>
      <button class="btn" type="submit">Email me a reset code</button>
    `;
  }
  return html`
    <label class="field"><span>Reset code</span>
      <input type="text" name="code" required autocomplete="one-time-code" placeholder="from the email"></label>
    <label class="field"><span>New password</span>
      <input type="password" name="new_password" required minlength="8" autocomplete="new-password"></label>
    <button class="btn" type="submit">Set password &amp; sign in</button>
  `;
}

function footNote() {
  if (mode === "signin") {
    // Withheld until /client-config has settled, so it never appears and then
    // vanishes. After that only an explicit false keeps it away: absent means a
    // server that never published the key, and those do send reset codes — the
    // same reading the iOS app takes. A fetch that failed settles to {} for
    // exactly that reason, so being unable to ask offers the link rather than
    // quietly removing one that works.
    if (config === null || config.password_reset_enabled === false) return html``;
    return html`<button class="link-btn" data-forgot>Forgotten your password?</button>`;
  }
  if (mode === "register") {
    return joinMode === "new"
      ? html`Your recipes, plan and list are visible only to your household.`
      : html`An invite code joins you to an existing household and everything in it.`;
  }
  return html`<button class="link-btn" data-back>Back to sign in</button>`;
}

async function submit(data, root) {
  if (mode === "signin") {
    const auth = await api("/auth/login", { method: "POST", body: data });
    session.save(auth);
    window.location.hash = "#/plan";
    return;
  }
  if (mode === "register") {
    const body = { email: data.email, password: data.password, display_name: data.display_name };
    if (joinMode === "invite") body.invite_code = data.invite_code.trim();
    else if (data.household_name?.trim()) body.household_name = data.household_name.trim();
    const auth = await api("/auth/register", { method: "POST", body });
    session.save(auth);
    window.location.hash = "#/plan";
    return;
  }
  if (resetStage === "request") {
    const accepted = await api("/auth/password-reset", { method: "POST", body: { email: data.email } });
    toast(accepted.detail, "ok");
    resetStage = "confirm";
    renderLogin(root);
    return;
  }
  const auth = await api("/auth/password/reset-confirm", {
    method: "POST",
    body: { code: data.code.trim(), new_password: data.new_password },
  });
  session.save(auth);
  toast("Password changed — you're signed in.", "ok");
  window.location.hash = "#/plan";
}
