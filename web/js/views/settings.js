// Settings: account, household invites (Q19), AI access tokens, and the
// danger zone (Q20). Invite codes and API tokens are shown exactly once —
// the server stores only hashes.

import { api, session } from "../api.js";
import { confirmDialog, fmtDate, fmtRel, html, openDialog, parseUtc, render, skeleton, toast } from "../dom.js";

export async function renderSettings(root) {
  render(root, skeleton());
  const [user, invites, tokens] = await Promise.all([
    api("/auth/me"),
    api("/auth/invites"),
    api("/auth/tokens"),
  ]);
  session.saveUser(user);

  render(root, html`
    <div class="page narrow">
      <div class="page-head">
        <div>
          <h1>Settings</h1>
          <p class="sub">${user.household_name || "Home"} · signed in as ${user.display_name}</p>
        </div>
      </div>

      <div class="section card">
        <h2>Household</h2>
        <p class="sub">Everyone in “${user.household_name || "Home"}” shares the recipes, plan and shopping list. Invite codes are single-use and expire; send one like you'd send a password.</p>
        ${invites.length > 0
          ? html`
              <table class="plain">
                <thead><tr><th>Created</th><th>Expires</th><th>Status</th><th></th></tr></thead>
                <tbody>
                  ${invites.map(
                    (invite) => html`
                      <tr>
                        <td>${fmtDate(invite.created_at)}</td>
                        <td>${fmtDate(invite.expires_at)}</td>
                        <td>${inviteStatus(invite)}</td>
                        <td>${!invite.accepted_at && parseUtc(invite.expires_at) > Date.now()
                          ? html`<button class="icon-btn warm" data-revoke="${invite.id}">revoke</button>`
                          : ""}</td>
                      </tr>
                    `,
                  )}
                </tbody>
              </table>
            `
          : html`<p class="sub">No invites issued yet.</p>`}
        <div class="dialog-actions"><button class="btn" data-invite>Invite someone</button></div>
      </div>

      <div class="section card">
        <h2>Your AI's key to the kitchen</h2>
        <p class="sub">
          A personal API token lets an assistant drive this server — the
          <a href="../skill" target="_blank" rel="noopener">skill</a> is its operating manual, the
          <a href="../prompt-pack" target="_blank" rel="noopener">prompt pack</a> a paste-anywhere version.
        </p>
        ${tokens.length > 0
          ? html`
              <table class="plain">
                <thead><tr><th>Label</th><th>Created</th><th>Last used</th><th>Expires</th><th></th></tr></thead>
                <tbody>
                  ${tokens.map(
                    (token) => html`
                      <tr>
                        <td>${token.label || "—"}</td>
                        <td>${fmtDate(token.created_at)}</td>
                        <td>${token.last_used_at ? fmtRel(token.last_used_at) : "never"}</td>
                        <td>${token.expires_at ? fmtDate(token.expires_at) : "never"}</td>
                        <td><button class="icon-btn warm" data-revoke-token="${token.id}">revoke</button></td>
                      </tr>
                    `,
                  )}
                </tbody>
              </table>
            `
          : html`<p class="sub">No API tokens yet.</p>`}
        <div class="dialog-actions"><button class="btn" data-token>New API token</button></div>
      </div>

      <div class="section card">
        <h2>Password</h2>
        <form data-password>
          <div class="form-row">
            <label class="field"><span>Current password</span>
              <input type="password" name="current_password" required autocomplete="current-password"></label>
            <label class="field"><span>New password</span>
              <input type="password" name="new_password" required minlength="8" autocomplete="new-password"></label>
          </div>
          <p class="sub">Other signed-in devices get logged out; API tokens deliberately survive.</p>
          <div class="dialog-actions"><button class="btn" type="submit">Change password</button></div>
        </form>
      </div>

      <div class="section card danger-zone">
        <h2>Delete account</h2>
        <p class="sub">
          Permanent, no grace period. If you're the last member the whole
          household goes — recipes, plans, history, all of it. Otherwise only
          you go, and the household keeps what it cooked.
        </p>
        <div class="dialog-actions"><button class="btn danger" data-delete-account>Delete my account…</button></div>
      </div>
    </div>
  `);

  root.querySelector("[data-invite]").onclick = async () => {
    const invite = await api("/auth/invites", { method: "POST", body: { expires_in_days: 7 } });
    revealDialog(
      "Invite code",
      invite.code,
      `Valid for one person, for 7 days. They register at this server with it and land in “${user.household_name || "Home"}”.`,
    );
    renderSettings(root);
  };

  for (const button of root.querySelectorAll("[data-revoke]")) {
    button.onclick = async () => {
      await api(`/auth/invites/${button.dataset.revoke}`, { method: "DELETE" });
      toast("Invite revoked.", "ok");
      renderSettings(root);
    };
  }

  root.querySelector("[data-token]").onclick = () => tokenDialog(root);

  for (const button of root.querySelectorAll("[data-revoke-token]")) {
    button.onclick = async () => {
      const ok = await confirmDialog({
        title: "Revoke this token?",
        body: "Whatever AI client holds it stops working immediately.",
        confirmLabel: "Revoke",
        danger: true,
      });
      if (!ok) return;
      await api(`/auth/tokens/${button.dataset.revokeToken}`, { method: "DELETE" });
      toast("Token revoked.", "ok");
      renderSettings(root);
    };
  }

  root.querySelector("[data-password]").onsubmit = async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target));
    try {
      const auth = await api("/auth/password", { method: "POST", body: data });
      session.save(auth); // the fresh token replaces the one just revoked
      toast("Password changed — this device stays signed in.", "ok");
      renderSettings(root);
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };

  root.querySelector("[data-delete-account]").onclick = () => deleteAccountDialog();
}

function inviteStatus(invite) {
  if (invite.accepted_at) return html`<span class="chip green">redeemed ${fmtRel(invite.accepted_at)}</span>`;
  if (parseUtc(invite.expires_at) <= Date.now()) return html`<span class="chip">expired</span>`;
  return html`<span class="chip butter">open</span>`;
}

function revealDialog(title, secret, note) {
  const dialog = openDialog(html`
    <h2>${title}</h2>
    <p class="sub">${note} Shown once — the server keeps only a hash.</p>
    <div class="copy-row">
      <div class="code-reveal">${secret}</div>
      <button class="btn ghost" data-copy>Copy</button>
    </div>
    <div class="dialog-actions"><button class="btn" data-x>Done</button></div>
  `);
  dialog.querySelector("[data-x]").onclick = () => dialog.close();
  dialog.querySelector("[data-copy]").onclick = async (event) => {
    await navigator.clipboard.writeText(secret);
    event.target.textContent = "Copied ✓";
  };
}

function tokenDialog(root) {
  const dialog = openDialog(html`
    <h2>New API token</h2>
    <form data-f>
      <label class="field"><span>Label</span>
        <input type="text" name="label" required placeholder="Claude on the laptop" autofocus></label>
      <label class="field"><span>Expires</span>
        <select name="expires_in_days">
          <option value="">never</option>
          <option value="30">in 30 days</option>
          <option value="90">in 90 days</option>
          <option value="365">in a year</option>
        </select></label>
      <div class="dialog-actions">
        <button class="btn ghost" type="button" data-x>Cancel</button>
        <button class="btn" type="submit">Create</button>
      </div>
    </form>
  `);
  dialog.querySelector("[data-x]").onclick = () => dialog.close();
  dialog.querySelector("[data-f]").onsubmit = async (event) => {
    event.preventDefault();
    const data = new FormData(event.target);
    const body = { label: data.get("label").trim() };
    if (data.get("expires_in_days")) body.expires_in_days = Number(data.get("expires_in_days"));
    try {
      const token = await api("/auth/tokens", { method: "POST", body });
      dialog.close();
      revealDialog("API token", token.token, "Give it to your assistant as a Bearer token.");
      renderSettings(root);
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };
}

function deleteAccountDialog() {
  const dialog = openDialog(html`
    <h2>Delete your account?</h2>
    <p class="sub">This cannot be undone. Type your password to confirm.</p>
    <form data-f>
      <label class="field"><span>Password</span>
        <input type="password" name="password" required autocomplete="current-password"></label>
      <div class="dialog-actions">
        <button class="btn ghost" type="button" data-x>Keep my account</button>
        <button class="btn danger" type="submit">Delete it</button>
      </div>
    </form>
  `);
  dialog.querySelector("[data-x]").onclick = () => dialog.close();
  dialog.querySelector("[data-f]").onsubmit = async (event) => {
    event.preventDefault();
    try {
      const result = await api("/auth/me", {
        method: "DELETE",
        body: { password: new FormData(event.target).get("password") },
      });
      dialog.close();
      session.clear();
      alert(result.detail); // last words before the login screen
      window.location.hash = "#/login";
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };
}
