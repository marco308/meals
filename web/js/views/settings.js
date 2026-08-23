// Settings: account, the household and who is in it (Q19/Q23), supermarkets &
// aisle order, AI access tokens, what this server allows, taking your data
// away, and the danger zone (Q20). Invite codes and API tokens are shown
// exactly once — the server stores only hashes.
//
// The lead is the only member who can invite, remove or rename (Q23), so those
// controls are hidden rather than shown-and-refused for everyone else. Leaving
// is nobody's business but your own, so that button is always there.

import { aisles, api, download, invalidateAisles, session } from "../api.js";
import { confirmDialog, fmtDate, fmtRel, html, openDialog, parseUtc, render, skeleton, toast } from "../dom.js";

export async function renderSettings(root) {
  render(root, skeleton());
  const [user, household, invites, tokens, markets, allowances, subscription] = await Promise.all([
    api("/auth/me"),
    api("/auth/household"),
    api("/auth/invites"),
    api("/auth/tokens"),
    api("/supermarkets"),
    api("/limits"),
    // 404 is the answer on every server that has no billing, which is almost
    // all of them, and it is the server's own answer rather than a guess.
    api("/billing/subscription").catch(() => null),
  ]);
  session.saveUser(user);
  const youLead = household.lead_user_id === user.id;
  const leadName = household.members.find((m) => m.is_lead)?.display_name || "whoever leads it";

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
        <p class="sub">
          Everyone in “${household.name}” shares the recipes, plan and shopping list, and can change
          any of it. ${youLead
            ? "You lead this household, so inviting and removing people is yours."
            : `${leadName} leads this household, so inviting and removing people is theirs.`}
        </p>
        <table class="plain">
          <thead><tr><th>Member</th><th>Joined</th><th></th></tr></thead>
          <tbody>
            ${household.members.map(
              (member) => html`
                <tr>
                  <td>
                    ${member.display_name}${member.id === user.id ? " (you)" : ""}
                    ${member.is_lead ? html`<span class="chip green">lead</span>` : ""}
                    <div class="sub">${member.email}</div>
                  </td>
                  <td>${fmtDate(member.created_at)}</td>
                  <td>${memberAction(member, user, youLead)}</td>
                </tr>
              `,
            )}
          </tbody>
        </table>
        <div class="dialog-actions">
          ${youLead ? html`<button class="btn ghost" data-rename-household>Rename household…</button>` : ""}
          ${youLead && household.members.length > 1
            ? html`<button class="btn ghost" data-hand-over>Hand over the lead…</button>`
            : ""}
          <button class="btn ghost" data-join-household>Join another household…</button>
        </div>
      </div>

      <div class="section card">
        <h2>Invites</h2>
        <p class="sub">
          Single-use, and they expire; send one like you'd send a password. Anyone holding a code gets
          everything the household has.${youLead ? "" : ` Only ${leadName} can issue or revoke them.`}
        </p>
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
                        <td>${youLead && !invite.accepted_at && parseUtc(invite.expires_at) > Date.now()
                          ? html`<button class="icon-btn warm" data-revoke="${invite.id}">revoke</button>`
                          : ""}</td>
                      </tr>
                    `,
                  )}
                </tbody>
              </table>
            `
          : html`<p class="sub">No invites issued yet.</p>`}
        ${youLead ? html`<div class="dialog-actions"><button class="btn" data-invite>Invite someone</button></div>` : ""}
      </div>

      ${allowancesSection(allowances)}

      ${subscriptionSection(subscription, household, youLead, leadName)}

      <div class="section card">
        <h2>Supermarkets &amp; aisle order</h2>
        <p class="sub">
          The shopping list walks the aisles in this order. Save the stores you
          actually shop at, arrange each one's aisles the way you meet them,
          and pick where you're shopping — every device (and your AI) sorts
          for it.
        </p>
        <div class="market-list">
          <div class="market-row">
            <label class="m-pick">
              <input type="radio" name="active-market" value="" ${markets.some((m) => m.is_active) ? "" : "checked"}>
              <div class="m-main">
                <b>Default order</b>
                <span class="sub">the built-in walk, fruit &amp; veg first</span>
              </div>
            </label>
          </div>
          ${markets.map(
            (m) => html`
              <div class="market-row">
                <label class="m-pick">
                  <input type="radio" name="active-market" value="${m.id}" ${m.is_active ? "checked" : ""}>
                  <div class="m-main">
                    <b>${m.name}</b>
                    <span class="sub aisle-mini">${m.aisle_order.join(" ")}</span>
                  </div>
                </label>
                <span class="m-actions">
                  <button class="icon-btn" type="button" data-order-market="${m.id}">aisle order</button>
                  <button class="icon-btn" type="button" data-rename-market="${m.id}">rename</button>
                  <button class="icon-btn warm" type="button" data-del-market="${m.id}">delete</button>
                </span>
              </div>
            `,
          )}
        </div>
        <div class="dialog-actions"><button class="btn" data-add-market>Add a supermarket</button></div>
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

      <div class="section card">
        <h2>Take everything with you</h2>
        <p class="sub">
          One file holding every recipe, ingredient, meal, plan, cooked shop and
          saved supermarket this household owns — the whole thing rather than a
          summary, and readable without joining anything back together.
          Passwords, API tokens and invite codes are deliberately left out.
          It is free of every limit on every server, because leaving should
          never be the hard part.
        </p>
        <div class="dialog-actions"><button class="btn ghost" data-export>Download everything</button></div>
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

  root.querySelector("[data-join-household]").onclick = () => joinHouseholdDialog(root, household);
  root.querySelector("[data-rename-household]")?.addEventListener("click", () =>
    renameHouseholdDialog(root, household),
  );
  root.querySelector("[data-hand-over]")?.addEventListener("click", () => handOverDialog(root, household, user));

  for (const button of root.querySelectorAll("[data-remove-member]")) {
    button.onclick = async () => {
      const member = household.members.find((m) => m.id === button.dataset.removeMember);
      const you = member.id === user.id;
      const ok = await confirmDialog({
        title: you ? `Leave ${household.name}?` : `Remove ${member.display_name}?`,
        body: you
          ? "Your account, your password and your API tokens all survive — you'll be in a household of your own, empty. The recipes, plan and history stay here."
          : `${member.display_name} keeps their account and lands in a household of their own, empty. Everything they added here stays.`,
        confirmLabel: you ? "Leave" : "Remove",
        danger: true,
      });
      if (!ok) return;
      try {
        const result = await api(`/auth/household/members/${member.id}`, { method: "DELETE" });
        toast(result.detail, "ok");
        if (result.you_left) {
          // Everything on screen belongs to a household we are no longer in.
          location.hash = "#/plan";
          location.reload();
          return;
        }
      } catch (error) {
        toast(error.detail || error.message, "error");
      }
      renderSettings(root);
    };
  }

  root.querySelector("[data-invite]")?.addEventListener("click", async () => {
    const invite = await api("/auth/invites", { method: "POST", body: { expires_in_days: 7 } });
    revealDialog(
      "Invite code",
      invite.code,
      `Valid for one person, for 7 days. They can register at this server with it, or paste it into Settings if they already have an account — either way they land in “${household.name}”.`,
    );
    renderSettings(root);
  });

  for (const button of root.querySelectorAll("[data-revoke]")) {
    button.onclick = async () => {
      await api(`/auth/invites/${button.dataset.revoke}`, { method: "DELETE" });
      toast("Invite revoked.", "ok");
      renderSettings(root);
    };
  }

  const activeMarketId = markets.find((m) => m.is_active)?.id || "";
  for (const radio of root.querySelectorAll('input[name="active-market"]')) {
    radio.onchange = async () => {
      try {
        if (radio.value) {
          await api(`/supermarkets/${radio.value}`, { method: "PATCH", body: { is_active: true } });
          toast(`Shopping list now sorts for ${markets.find((m) => m.id === radio.value)?.name}.`, "ok");
        } else if (activeMarketId) {
          await api(`/supermarkets/${activeMarketId}`, { method: "PATCH", body: { is_active: false } });
          toast("Back to the default aisle order.", "ok");
        }
        invalidateAisles();
      } catch (error) {
        toast(error.detail || error.message, "error");
      }
      renderSettings(root);
    };
  }

  root.querySelector("[data-add-market]").onclick = () => addMarketDialog(root);

  for (const button of root.querySelectorAll("[data-order-market]")) {
    button.onclick = () => orderDialog(root, markets.find((m) => m.id === button.dataset.orderMarket));
  }

  for (const button of root.querySelectorAll("[data-rename-market]")) {
    button.onclick = () => renameMarketDialog(root, markets.find((m) => m.id === button.dataset.renameMarket));
  }

  for (const button of root.querySelectorAll("[data-del-market]")) {
    button.onclick = async () => {
      const market = markets.find((m) => m.id === button.dataset.delMarket);
      const ok = await confirmDialog({
        title: `Delete ${market.name}?`,
        body: market.is_active
          ? "Its saved aisle order goes with it and the list goes back to the default order."
          : "Its saved aisle order goes with it.",
        confirmLabel: "Delete",
        danger: true,
      });
      if (!ok) return;
      await api(`/supermarkets/${market.id}`, { method: "DELETE" });
      invalidateAisles();
      toast(`${market.name} deleted.`, "ok");
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

  // The bars are the one place a width is computed rather than written, and
  // index.html's `style-src 'self'` forbids a style *attribute*, so the number
  // rides on a data attribute and is applied through the CSSOM, which CSP
  // deliberately does not police.
  for (const fill of root.querySelectorAll("[data-fill]")) fill.style.width = `${fill.dataset.fill}%`;

  const subscribe = root.querySelector("[data-subscribe]");
  if (subscribe) {
    subscribe.onclick = async () => {
      subscribe.disabled = true;
      try {
        // The URL is the processor's, single-use, and bound to this household.
        // Leaving the app for it is the whole design: no commerce is served here.
        const checkout = await api("/billing/checkout", { method: "POST" });
        window.location.assign(checkout.url);
      } catch (error) {
        toast(error.detail || error.message, "error");
        subscribe.disabled = false;
      }
    };
  }

  root.querySelector("[data-export]").onclick = async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await download("/household/export");
      toast("Your household is on its way to your downloads.", "ok");
    } catch (error) {
      toast(error.detail || error.message, "error");
    } finally {
      button.disabled = false;
    }
  };

  root.querySelector("[data-delete-account]").onclick = () => deleteAccountDialog();
}

// ── the subscription ─────────────────────────────────────────────────────
//
// The only commerce in this project, and it is here rather than in the iPhone
// app on purpose (planning/08-freemium.md §6): the app carries no price, no
// button and no link to one, and the App Store listing rests on that.
//
// `GET /billing/subscription` 404s on every server with no billing configured,
// which is almost all of them, so a null here means the card is absent — the
// same rule the allowances card follows, from the same §1.
//
// Paying leaves this app entirely. The processor is the merchant of record, so
// the checkout, the card details, the invoices and any refund are theirs, and
// this server never sees a card number.

function subscriptionSection(subscription, household, youLead, leadName) {
  if (!subscription) return "";
  return html`
    <div class="section card">
      <h2>Subscription</h2>
      <p class="sub">${subscriptionState(subscription)}</p>
      ${subscription.can_checkout && !youLead
        ? html`<p class="sub">${leadName} leads “${household.name}”, so this is theirs to arrange.</p>`
        : ""}
      <div class="dialog-actions">
        ${subscription.can_checkout && youLead
          ? html`<button class="btn" data-subscribe>${subscribeLabel(subscription)}</button>`
          : ""}
        ${subscription.manage_url
          ? html`<a class="btn ghost" href="${subscription.manage_url}" target="_blank" rel="noopener">
              Manage billing
            </a>`
          : ""}
      </div>
    </div>
  `;
}

function subscriptionState(subscription) {
  const paid = money(subscription.price_pence, subscription.price_currency);
  const from = subscription.source === "comp" ? ", with the compliments of whoever runs it" : "";
  switch (subscription.state) {
    case "paid":
      return `Paid until ${fmtDate(subscription.paid_until)}${from}${paid ? `, at ${paid} a year` : ""}.`;
    case "grace":
      // §5: lapsing reduces what a household can add, and takes nothing away.
      return `This ran out on ${fmtDate(subscription.paid_until)}. The free tier's limits come back on
        ${fmtDate(subscription.grace_ends_at)} and nothing is deleted, then or ever.`;
    case "lapsed":
      return `This ran out on ${fmtDate(subscription.paid_until)}, so the free tier's limits apply again.
        Everything already here stayed, and the shopping list never stopped.`;
    default:
      // No expiry at all: a standing comp, or a household that has never paid.
      return subscription.source
        ? "This household is paid up, with no expiry."
        : "This household pays nothing here.";
  }
}

function subscribeLabel(subscription) {
  const offer = money(subscription.offer_price_pence, subscription.offer_price_currency);
  return offer ? `Subscribe — ${offer} a year` : "Subscribe";
}

function money(pence, currency) {
  if (pence === null || pence === undefined) return "";
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: currency || "GBP",
    minimumFractionDigits: pence % 100 === 0 ? 0 : 2,
  }).format(pence / 100);
}

// ── what this server allows ──────────────────────────────────────────────
//
// planning/08-freemium.md §1: a quota on somebody's hosting, never a fence
// around the tool. On a server that has configured nothing, this card is
// absent — not empty, not a row of "unlimited", absent — and `limited` from
// GET /limits is the switch that decides it. Reading the flag rather than
// inferring it from the numbers is deliberate: a household comped to the
// unlimited tier on a server that *does* limit things has every number null
// and still deserves to be told where it stands.
//
// Nothing here names a price or points anywhere to pay one. Whether this
// server sells anything at all is a question GET /limits cannot answer, and
// answering it is issue #121's job rather than this card's.

const RESOURCE_LABELS = {
  members: "People in this household",
  recipes: "Recipes",
  ingredients: "Ingredients",
  meals: "Meals",
  meal_lines: "Lines in one meal",
  plans: "Plans on the go",
  plan_meals: "Meals in one plan",
  supermarkets: "Supermarkets",
  api_tokens: "API tokens",
  ingests_per_month: "Recipes read from a URL",
};

// A server vocabulary, so an unknown name must render rather than vanish —
// the same rule that keeps aisles and slots out of the iOS enums.
const resourceLabel = (name) => RESOURCE_LABELS[name] || name.replace(/_/g, " ");

function allowancesSection(allowances) {
  if (!allowances.limited) return "";
  const capped = allowances.resources.filter((row) => row.limit !== null);
  return html`
    <div class="section card">
      <h2>What this server allows</h2>
      <p class="sub">
        ${capped.length === 0
          ? "This server sets limits, and none of them apply to this household."
          : html`Nothing you have already saved is ever removed by a limit, and the shopping list is
              never limited at all — a cap only ever stops something new being added.`}
      </p>
      ${capped.length === 0 ? "" : html`<div class="allow-list">${capped.map(allowanceRow)}</div>`}
    </div>
  `;
}

function allowanceRow(row) {
  // `used` is null where a household-wide count would mean nothing: the
  // per-meal and per-plan allowances bound the next one rather than tally what
  // exists, so they get the number alone and no bar to fill.
  if (row.used === null) {
    return html`
      <div class="allow-row">
        <div class="a-main"><b>${resourceLabel(row.resource)}</b></div>
        <span class="a-cap">up to ${row.limit}</span>
      </div>
    `;
  }
  const percent = Math.min(100, Math.round((row.used / row.limit) * 100));
  return html`
    <div class="allow-row">
      <div class="a-main">
        <b>${resourceLabel(row.resource)}</b>
        <span class="sub">${allowanceUsage(row)}</span>
      </div>
      <div class="a-bar ${row.remaining === 0 ? "full" : ""}"><span data-fill="${percent}"></span></div>
    </div>
  `;
}

function allowanceUsage(row) {
  const period = row.scope === "a month" ? " this month" : "";
  if (row.remaining !== 0) return `${row.used} of ${row.limit} used${period} · ${row.remaining} left`;
  // At the wall is the one moment the difference between the two kinds of
  // limit (§4) is worth a sentence: one is this household's tier, the other is
  // this server's own ceiling, which no tier lifts.
  return row.upgradable
    ? `${row.used} of ${row.limit} used${period} — at the limit for this household's tier`
    : `${row.used} of ${row.limit} used${period} — at the most this server allows`;
}

function addMarketDialog(root) {
  const dialog = openDialog(html`
    <h2>Add a supermarket</h2>
    <p class="sub">It starts on the default aisle order — you'll arrange its own walk next.</p>
    <form data-f>
      <label class="field"><span>Name</span>
        <input type="text" name="name" required maxlength="120" placeholder="Big Tesco" autofocus></label>
      <div class="dialog-actions">
        <button class="btn ghost" type="button" data-x>Cancel</button>
        <button class="btn" type="submit">Add</button>
      </div>
    </form>
  `);
  dialog.querySelector("[data-x]").onclick = () => dialog.close();
  dialog.querySelector("[data-f]").onsubmit = async (event) => {
    event.preventDefault();
    const name = new FormData(event.target).get("name").trim();
    if (!name) return;
    try {
      const market = await api("/supermarkets", { method: "POST", body: { name } });
      dialog.close();
      orderDialog(root, market); // straight into arranging the walk
    } catch (error) {
      toast(error.detail || error.message, "error"); // e.g. the duplicate-name 409
    }
  };
}

async function orderDialog(root, market) {
  const labels = new Map((await aisles()).map((a) => [a.emoji, a.label]));
  const order = [...market.aisle_order];
  const dialog = openDialog(html`
    <h2>${market.name} — aisle order</h2>
    <p class="sub">First aisle you meet at the top; the shopping list walks it top to bottom.</p>
    <div class="order-rows" data-rows></div>
    <div class="dialog-actions">
      <button class="btn ghost" type="button" data-x>Cancel</button>
      <button class="btn" type="button" data-save>Save order</button>
    </div>
  `);
  const rows = dialog.querySelector("[data-rows]");

  const move = (from, to, dir) => {
    [order[from], order[to]] = [order[to], order[from]];
    draw();
    // Keep focus on the moved aisle's button so keyboard reordering flows.
    const same = rows.querySelector(`[data-${dir}="${to}"]`);
    const other = rows.querySelector(`[data-${dir === "up" ? "down" : "up"}="${to}"]`);
    (same && !same.disabled ? same : other)?.focus();
  };

  const draw = () => {
    render(
      rows,
      html`${order.map(
        (emoji, i) => html`
          <div class="order-row">
            <span class="o-emoji" aria-hidden="true">${emoji}</span>
            <span class="o-label">${labels.get(emoji) || "Unknown"}</span>
            <span class="o-btns">
              <button class="icon-btn" type="button" data-up="${i}" ${i === 0 ? "disabled" : ""}
                aria-label="Move ${labels.get(emoji) || emoji} up">↑</button>
              <button class="icon-btn" type="button" data-down="${i}" ${i === order.length - 1 ? "disabled" : ""}
                aria-label="Move ${labels.get(emoji) || emoji} down">↓</button>
            </span>
          </div>
        `,
      )}`,
    );
    for (const button of rows.querySelectorAll("[data-up]")) {
      button.onclick = () => move(Number(button.dataset.up), Number(button.dataset.up) - 1, "up");
    }
    for (const button of rows.querySelectorAll("[data-down]")) {
      button.onclick = () => move(Number(button.dataset.down), Number(button.dataset.down) + 1, "down");
    }
  };
  draw();

  dialog.querySelector("[data-x]").onclick = () => dialog.close();
  dialog.querySelector("[data-save]").onclick = async () => {
    try {
      await api(`/supermarkets/${market.id}`, { method: "PATCH", body: { aisle_order: order } });
      invalidateAisles();
      dialog.close();
      toast(`${market.name}'s aisle order saved.`, "ok");
      renderSettings(root);
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };
}

function renameMarketDialog(root, market) {
  const dialog = openDialog(html`
    <h2>Rename ${market.name}</h2>
    <form data-f>
      <label class="field"><span>Name</span>
        <input type="text" name="name" required maxlength="120" value="${market.name}" autofocus></label>
      <div class="dialog-actions">
        <button class="btn ghost" type="button" data-x>Cancel</button>
        <button class="btn" type="submit">Rename</button>
      </div>
    </form>
  `);
  dialog.querySelector("[data-x]").onclick = () => dialog.close();
  dialog.querySelector("[data-f]").onsubmit = async (event) => {
    event.preventDefault();
    const name = new FormData(event.target).get("name").trim();
    if (!name) return;
    try {
      await api(`/supermarkets/${market.id}`, { method: "PATCH", body: { name } });
      dialog.close();
      toast("Renamed.", "ok");
      renderSettings(root);
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };
}

function memberAction(member, user, youLead) {
  // Leaving is yours whoever you are; removing someone else is the lead's.
  if (member.id === user.id) {
    return html`<button class="icon-btn warm" data-remove-member="${member.id}">leave</button>`;
  }
  if (!youLead) return "";
  return html`<button class="icon-btn warm" data-remove-member="${member.id}">remove</button>`;
}

function renameHouseholdDialog(root, household) {
  const dialog = openDialog(html`
    <h2>Rename ${household.name}</h2>
    <form data-f>
      <label class="field"><span>Name</span>
        <input type="text" name="name" required maxlength="200" value="${household.name}" autofocus></label>
      <div class="dialog-actions">
        <button class="btn ghost" type="button" data-x>Cancel</button>
        <button class="btn" type="submit">Rename</button>
      </div>
    </form>
  `);
  dialog.querySelector("[data-x]").onclick = () => dialog.close();
  dialog.querySelector("[data-f]").onsubmit = async (event) => {
    event.preventDefault();
    const name = new FormData(event.target).get("name").trim();
    if (!name) return;
    try {
      await api("/auth/household", { method: "PATCH", body: { name } });
      dialog.close();
      toast("Renamed.", "ok");
      renderSettings(root);
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };
}

function handOverDialog(root, household, user) {
  const others = household.members.filter((member) => member.id !== user.id);
  const dialog = openDialog(html`
    <h2>Hand over the lead</h2>
    <p class="sub">
      They get the invites and the guest list; you become an ordinary member and can then leave if you
      want to. Everything about the recipes, plan and list is unchanged for both of you.
    </p>
    <form data-f>
      <label class="field"><span>New lead</span>
        <select name="lead_user_id">
          ${others.map((member) => html`<option value="${member.id}">${member.display_name}</option>`)}
        </select></label>
      <div class="dialog-actions">
        <button class="btn ghost" type="button" data-x>Cancel</button>
        <button class="btn" type="submit">Hand over</button>
      </div>
    </form>
  `);
  dialog.querySelector("[data-x]").onclick = () => dialog.close();
  dialog.querySelector("[data-f]").onsubmit = async (event) => {
    event.preventDefault();
    const leadUserId = new FormData(event.target).get("lead_user_id");
    try {
      await api("/auth/household", { method: "PATCH", body: { lead_user_id: leadUserId } });
      dialog.close();
      toast("Handed over.", "ok");
      renderSettings(root);
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };
}

function joinHouseholdDialog(root, household) {
  const dialog = openDialog(html`
    <h2>Join another household</h2>
    <p class="sub">
      Paste a code somebody sent you. You keep this account and everything signed in on it — only which
      household you're in changes. “${household.name}” keeps its recipes unless you're its only member,
      in which case they go with you.
    </p>
    <form data-f>
      <label class="field"><span>Invite code</span>
        <input type="text" name="code" required maxlength="64" placeholder="XXXX-XXXX-XXXX" autofocus></label>
      <div class="dialog-actions">
        <button class="btn ghost" type="button" data-x>Cancel</button>
        <button class="btn" type="submit">Join</button>
      </div>
    </form>
  `);
  dialog.querySelector("[data-x]").onclick = () => dialog.close();
  dialog.querySelector("[data-f]").onsubmit = async (event) => {
    event.preventDefault();
    const code = new FormData(event.target).get("code").trim();
    if (!code) return;
    const join = async (force) => api("/auth/invites/redeem", { method: "POST", body: { code, force } });
    try {
      let joined;
      try {
        joined = await join(false);
      } catch (error) {
        // The 409 is the server saying this would delete a library nobody else
        // can reach. Ask, then say so explicitly rather than retrying quietly.
        if (error.status !== 409 || !String(error.detail || "").includes("force")) throw error;
        const ok = await confirmDialog({
          title: `Give up ${household.name}?`,
          body: error.detail,
          confirmLabel: "Join anyway",
          danger: true,
        });
        if (!ok) return;
        joined = await join(true);
      }
      dialog.close();
      toast(`You're now in “${joined.household_name || "Home"}”.`, "ok");
      location.hash = "#/plan";
      location.reload();
    } catch (error) {
      toast(error.detail || error.message, "error");
    }
  };
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
