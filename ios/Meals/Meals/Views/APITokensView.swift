import SwiftUI

/// Your AI's key to the kitchen: personal API tokens that let an assistant
/// drive this server as you. Minted here, shown once (the server keeps only a
/// hash), revocable any time — revoking cuts that assistant off immediately.
struct APITokensView: View {
    @Environment(Session.self) private var session

    @State private var tokens: [APIToken] = []
    @State private var loaded = false
    @State private var errorMessage: String?
    @State private var showCreate = false
    @State private var pendingRevoke: APIToken?

    var body: some View {
        List {
            Section {
                ForEach(tokens) { token in
                    tokenRow(token)
                }
                if loaded && tokens.isEmpty {
                    Text("No API tokens yet.")
                        .foregroundStyle(.secondary)
                }
            } footer: {
                Text(
                    "A token lets an assistant use this server with everything your account "
                        + "can see and do. The skill is its operating manual; the prompt pack "
                        + "is a paste-anywhere version."
                )
            }

            Section {
                Button {
                    showCreate = true
                } label: {
                    Label("New API token", systemImage: "plus")
                }
                Link(destination: AppLinks.skill(server: session.serverURL)) {
                    Label("The skill", systemImage: "text.book.closed")
                }
                Link(destination: AppLinks.promptPack(server: session.serverURL)) {
                    Label("The prompt pack", systemImage: "doc.text")
                }
            }

            if let errorMessage {
                Section {
                    Text(errorMessage).foregroundStyle(.red).font(.callout)
                }
            }
        }
        .navigationTitle("AI access")
        .navigationBarTitleDisplayMode(.inline)
        .task { await refresh() }
        .refreshable { await refresh() }
        .sheet(isPresented: $showCreate) {
            CreateTokenSheet {
                await refresh()
            }
        }
        .confirmationDialog(
            "Revoke '\(pendingRevoke?.label ?? "this token")'? Whatever AI client holds it stops working immediately.",
            isPresented: .init(get: { pendingRevoke != nil }, set: { if !$0 { pendingRevoke = nil } }),
            titleVisibility: .visible
        ) {
            Button("Revoke token", role: .destructive) {
                guard let token = pendingRevoke else { return }
                pendingRevoke = nil
                Task { await revoke(token) }
            }
        }
    }

    private func tokenRow(_ token: APIToken) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(token.label ?? "Unlabelled token")
            Text(detailLine(token))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button(role: .destructive) {
                pendingRevoke = token
            } label: {
                Label("Revoke", systemImage: "xmark.circle")
            }
        }
    }

    private func detailLine(_ token: APIToken) -> String {
        var parts: [String] = []
        if let created = TimestampLabel.day(token.createdAt) { parts.append("created \(created)") }
        if let used = TimestampLabel.day(token.lastUsedAt) {
            parts.append("last used \(used)")
        } else {
            parts.append("never used")
        }
        if let expires = TimestampLabel.day(token.expiresAt) {
            parts.append("expires \(expires)")
        } else {
            parts.append("never expires")
        }
        return parts.joined(separator: " · ")
    }

    private func refresh() async {
        do {
            tokens = try await session.api.apiTokens()
            loaded = true
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func revoke(_ token: APIToken) async {
        do {
            try await session.api.revokeAPIToken(id: token.id)
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

/// Mint a token and show it once — the same shown-like-a-password treatment
/// the invite sheet gives its code, because it grants the same reach.
struct CreateTokenSheet: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    /// Runs after a successful creation, so the list behind refreshes.
    let onCreated: () async -> Void

    @State private var label = ""
    @State private var expiresInDays: Int?
    @State private var created: APITokenCreated?
    @State private var isWorking = false
    @State private var errorMessage: String?

    private static let expiryOptions: [(label: String, days: Int?)] = [
        ("Never", nil), ("In 30 days", 30), ("In 90 days", 90), ("In a year", 365),
    ]

    var body: some View {
        NavigationStack {
            Form {
                if let created {
                    Section {
                        Text(created.token)
                            .font(.system(.callout, design: .monospaced, weight: .semibold))
                            .frame(maxWidth: .infinity, alignment: .center)
                            .textSelection(.enabled)
                            .padding(.vertical, 8)
                        ShareLink(item: created.token) {
                            Label("Share token", systemImage: "square.and.arrow.up")
                        }
                    } footer: {
                        Text("Give it to your assistant as a Bearer token. Shown once — the server keeps only a hash.")
                    }
                } else {
                    Section {
                        TextField("Label (e.g. Claude on the laptop)", text: $label)
                        Picker("Expires", selection: $expiresInDays) {
                            ForEach(Self.expiryOptions, id: \.days) { option in
                                Text(option.label).tag(option.days)
                            }
                        }
                    } footer: {
                        Text("The label is how you'll recognise it in the list when it's time to revoke it.")
                    }

                    if let errorMessage {
                        Section {
                            Text(errorMessage).foregroundStyle(.red).font(.callout)
                        }
                    }

                    Section {
                        Button(action: create) {
                            if isWorking {
                                ProgressView().frame(maxWidth: .infinity)
                            } else {
                                Text("Create token").frame(maxWidth: .infinity).fontWeight(.semibold)
                            }
                        }
                        .disabled(isWorking || label.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            }
            .navigationTitle("New API token")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button(created == nil ? "Cancel" : "Done") { dismiss() }
                }
            }
            // The token is unrecoverable once this closes; make dismissal a
            // deliberate tap on Done, not an accidental swipe.
            .interactiveDismissDisabled(created != nil)
        }
    }

    private func create() {
        isWorking = true
        errorMessage = nil
        Task {
            defer { isWorking = false }
            do {
                created = try await session.api.createAPIToken(
                    label: label.trimmingCharacters(in: .whitespaces),
                    expiresInDays: expiresInDays
                )
                await onCreated()
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
