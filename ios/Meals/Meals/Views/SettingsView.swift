import SwiftUI

/// Account, household and app information.
///
/// This is a tab rather than a menu item because two of the things on it —
/// account deletion and the privacy policy — are things App Review expects a
/// user to be able to find without hunting, and because "which server am I
/// even talking to?" is a question only this app makes people ask.
struct SettingsView: View {
    @Environment(Session.self) private var session
    @Environment(PlanStore.self) private var planStore
    @Environment(RecipeStore.self) private var recipeStore

    @State private var showChangePassword = false
    @State private var showDeleteAccount = false
    @State private var showInvite = false

    var body: some View {
        NavigationStack {
            List {
                accountSection
                householdSection
                serverSection
                aboutSection
                dangerSection
            }
            .navigationTitle("Settings")
            .sheet(isPresented: $showChangePassword) { ChangePasswordView() }
            .sheet(isPresented: $showInvite) { InviteSheet() }
            .sheet(isPresented: $showDeleteAccount) {
                DeleteAccountView(clearCaches: clearCaches)
            }
            .task { await session.restore() }
        }
    }

    private var accountSection: some View {
        Section("Account") {
            if let user = session.user {
                LabeledContent("Name", value: user.displayName)
                LabeledContent("Email", value: user.email)
            } else {
                // restore() is silent when offline, so this is "we haven't been
                // able to ask", not "you're signed out".
                Text("Signed in").foregroundStyle(.secondary)
            }
            Button("Change password…") { showChangePassword = true }
        }
    }

    private var householdSection: some View {
        Section {
            if let name = session.user?.householdName {
                LabeledContent("Household", value: name)
            }
            Button {
                showInvite = true
            } label: {
                Label("Invite someone…", systemImage: "person.badge.plus")
            }
        } footer: {
            Text(
                "Everyone in a household shares its recipes, plan and shopping list, "
                    + "and can change all of it. There are no roles or permissions."
            )
        }
    }

    private var serverSection: some View {
        Section {
            LabeledContent("Server") {
                Text(session.serverURL)
                    .font(.callout.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
        } header: {
            Text("Server")
        } footer: {
            // Editable here would be a trap: the token in the keychain belongs
            // to *this* server, so moving would sign you out anyway.
            Text("Your data lives here, and nowhere else. Sign out to connect to a different server.")
        }
    }

    private var aboutSection: some View {
        Section("About") {
            LabeledContent("Version", value: ClientIdentity.displayVersion)
            Link(destination: AppLinks.privacy(server: session.serverURL)) {
                Label("Privacy policy", systemImage: "hand.raised")
            }
            Link(destination: AppLinks.support(server: session.serverURL)) {
                Label("Help & support", systemImage: "questionmark.circle")
            }
            Link(destination: AppLinks.sourceCode) {
                Label("Source code", systemImage: "chevron.left.forwardslash.chevron.right")
            }
        }
    }

    private var dangerSection: some View {
        Section {
            Button("Log out") {
                clearCaches()
                session.logOut()
            }
            Button("Delete account…", role: .destructive) { showDeleteAccount = true }
        }
    }

    /// Cached reads belong to the login that fetched them — a stale plan must
    /// not outlive it. The shopping list is deliberately left alone: it holds
    /// the offline queue, and dropping that would destroy unsynced changes.
    private func clearCaches() {
        planStore.clearCache()
        recipeStore.clearCache()
    }
}

/// Mints an invite code and shows it once.
///
/// The code is the whole authorisation boundary — anyone holding it gets the
/// household's entire contents — so it reads like a password here: shown once,
/// never recoverable, with the consequence stated before the share button.
struct InviteSheet: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    @State private var invite: InviteCreated?
    @State private var isWorking = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                if let invite {
                    Section {
                        Text(invite.code)
                            .font(.system(.title, design: .monospaced, weight: .semibold))
                            .frame(maxWidth: .infinity, alignment: .center)
                            .textSelection(.enabled)
                            .padding(.vertical, 8)
                        ShareLink(item: shareText(invite.code)) {
                            Label("Share code", systemImage: "square.and.arrow.up")
                        }
                    } footer: {
                        Text(expiryFooter(invite))
                    }

                    Section {
                        Text(
                            "Anyone with this code can read and change everything in your household. "
                                + "Send it the way you'd send a password — and it isn't shown again."
                        )
                        .font(.callout)
                    }
                } else if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.red).font(.callout)
                        Button("Try again") { Task { await mint() } }
                    }
                } else {
                    Section {
                        HStack {
                            ProgressView()
                            Text("Creating a code…").foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .navigationTitle("Invite someone")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .task {
                // Only once: re-entering the task on a redraw would burn a
                // fresh single-use code every time.
                if invite == nil && !isWorking { await mint() }
            }
        }
    }

    private func expiryFooter(_ invite: InviteCreated) -> String {
        let lifetime = invite.expiryLabel.map { "Single use, expires \($0)." } ?? "Single use."
        return lifetime + " They enter it when creating their account, along with this server's address."
    }

    private func shareText(_ code: String) -> String {
        "Join my Meals household. Install Meals, set the server to \(session.serverURL), "
            + "create an account and enter this invite code: \(code)"
    }

    private func mint() async {
        isWorking = true
        errorMessage = nil
        defer { isWorking = false }
        do {
            invite = try await session.api.createInvite()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
