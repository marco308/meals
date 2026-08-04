import SwiftUI

/// The household's invites: who could still join, and the paper trail of who
/// did. Codes are hashed the moment they're minted, so nothing here can leak
/// one — to let someone in, mint a fresh code; to change your mind about an
/// open one, revoke it.
struct InvitesView: View {
    @Environment(Session.self) private var session

    @State private var invites: [InviteInfo] = []
    @State private var loaded = false
    @State private var errorMessage: String?
    @State private var showInvite = false
    @State private var pendingRevoke: InviteInfo?

    var body: some View {
        List {
            Section {
                ForEach(invites) { invite in
                    inviteRow(invite)
                }
                if loaded && invites.isEmpty {
                    Text("No invites issued yet.")
                        .foregroundStyle(.secondary)
                }
            } footer: {
                Text(
                    "Invite codes are single-use and expire; send one like you'd send a "
                        + "password. Revoking an open invite means its code no longer works."
                )
            }

            Section {
                Button {
                    showInvite = true
                } label: {
                    Label("Invite someone", systemImage: "person.badge.plus")
                }
            }

            if let errorMessage {
                Section {
                    Text(errorMessage).foregroundStyle(.red).font(.callout)
                }
            }
        }
        .navigationTitle("Invites")
        .navigationBarTitleDisplayMode(.inline)
        .task { await refresh() }
        .refreshable { await refresh() }
        .sheet(isPresented: $showInvite, onDismiss: { Task { await refresh() } }) {
            InviteSheet()
        }
        .confirmationDialog(
            "Revoke this invite? Its code stops working — whoever you sent it to can no longer join.",
            isPresented: .init(get: { pendingRevoke != nil }, set: { if !$0 { pendingRevoke = nil } }),
            titleVisibility: .visible
        ) {
            Button("Revoke invite", role: .destructive) {
                guard let invite = pendingRevoke else { return }
                pendingRevoke = nil
                Task { await revoke(invite) }
            }
        }
    }

    @ViewBuilder
    private func inviteRow(_ invite: InviteInfo) -> some View {
        let status = invite.status()
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                switch status {
                case .open:
                    Image(systemName: "envelope")
                        .foregroundStyle(.tint)
                    Text("Open")
                case .redeemed:
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                    Text("Redeemed")
                case .expired:
                    Image(systemName: "clock.badge.xmark")
                        .foregroundStyle(.secondary)
                    Text("Expired").foregroundStyle(.secondary)
                }
            }
            Text(detailLine(invite, status: status))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            if status == .open {
                Button(role: .destructive) {
                    pendingRevoke = invite
                } label: {
                    Label("Revoke", systemImage: "xmark.circle")
                }
            }
        }
    }

    private func detailLine(_ invite: InviteInfo, status: InviteInfo.Status) -> String {
        var parts: [String] = []
        if let created = TimestampLabel.day(invite.createdAt) { parts.append("created \(created)") }
        switch status {
        case .open:
            if let expires = TimestampLabel.day(invite.expiresAt) { parts.append("expires \(expires)") }
        case .redeemed:
            if let redeemed = TimestampLabel.day(invite.acceptedAt) { parts.append("redeemed \(redeemed)") }
        case .expired:
            if let expired = TimestampLabel.day(invite.expiresAt) { parts.append("expired \(expired)") }
        }
        return parts.joined(separator: " · ")
    }

    private func refresh() async {
        do {
            invites = try await session.api.invites()
            loaded = true
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func revoke(_ invite: InviteInfo) async {
        do {
            try await session.api.revokeInvite(id: invite.id)
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
