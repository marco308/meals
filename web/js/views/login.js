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
        <p class="login-foot">${footNote()}</p>
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
  const forgot = root.querySelector("[data-forgot]");
  if (forgot) {
    forgot.onclick = () => {
      mode = "reset";
      resetStage = "request";
      renderLogin(root);
    };
  }
  const back = root.querySelector("[data-back]");
  if (back) {
    back.onclick = () => {
      mode = "signin";
      renderLogin(root);
    };
  }
  for (const seg of root.querySelectorAll("[data-join]")) {
    seg.onclick = () => {
      joinMode = seg.dataset.join;
      renderLogin(root);
    };
  }

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
  if (mode === "signin") return html`<button class="link-btn" data-forgot>Forgotten your password?</button>`;
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
