import SwiftUI

/// Who is in the household, and the things you can do about it (decision Q23).
///
/// Everyone here shares one recipe library, plan and shopping list and can
/// change every bit of it. The one thing that isn't equal is the guest list:
/// inviting, removing and renaming belong to the **lead**, the member the
/// household is billed to. Leaving is nobody's business but your own, so that
/// row is always here — a household you could only get out of by deleting your
/// account would be a worse trap than the one this screen exists to open.
struct HouseholdView: View {
    @Environment(Session.self) private var session
    @Environment(PlanStore.self) private var planStore
    @Environment(RecipeStore.self) private var recipeStore

    @State private var household: Household?
    @State private var loaded = false
    @State private var errorMessage: String?
    @State private var showRename = false
    @State private var showHandOver = false
    @State private var showJoin = false
    @State private var pendingRemoval: HouseholdMember?

    private var youLead: Bool { session.user?.leadsHousehold ?? false }
    private var leadName: String {
        household?.members.first(where: { $0.isLead })?.displayName ?? "whoever leads it"
    }

    var body: some View {
        List {
            membersSection
            actionsSection
            if let errorMessage {
                Section {
                    Text(errorMessage).foregroundStyle(.red).font(.callout)
                }
            }
        }
        .navigationTitle(household?.name ?? "Household")
        .navigationBarTitleDisplayMode(.inline)
        .task { await refresh() }
        .refreshable { await refresh() }
        .sheet(isPresented: $showRename) {
            RenameHouseholdSheet(currentName: household?.name ?? "") { await refresh() }
        }
        .sheet(isPresented: $showHandOver) {
            HandOverLeadSheet(members: others) { await refresh() }
        }
        .sheet(isPresented: $showJoin) {
            JoinHouseholdSheet(currentName: household?.name ?? "this household", clearCaches: clearCaches)
        }
        .confirmationDialog(
            removalPrompt,
            isPresented: .init(get: { pendingRemoval != nil }, set: { if !$0 { pendingRemoval = nil } }),
            titleVisibility: .visible
        ) {
            if let member = pendingRemoval {
                Button(member.id == session.user?.id ? "Leave" : "Remove", role: .destructive) {
                    pendingRemoval = nil
                    Task { await remove(member) }
                }
            }
        }
    }

    private var others: [HouseholdMember] {
        (household?.members ?? []).filter { $0.id != session.user?.id }
    }

    private var membersSection: some View {
        Section {
            ForEach(household?.members ?? []) { member in
                memberRow(member)
            }
            if loaded && household == nil {
                Text("Couldn't load the household.").foregroundStyle(.secondary)
            }
        } header: {
            Text("Members")
        } footer: {
            Text(
                youLead
                    ? "Everyone here shares the recipes, plan and shopping list, and can change all of it. "
                        + "You lead this household, so inviting and removing people is yours."
                    : "Everyone here shares the recipes, plan and shopping list, and can change all of it. "
                        + "\(leadName) leads this household, so inviting and removing people is theirs."
            )
        }
    }

    @ViewBuilder
    private func memberRow(_ member: HouseholdMember) -> some View {
        let you = member.id == session.user?.id
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(you ? "\(member.displayName) (you)" : member.displayName)
                if member.isLead {
                    Text("Lead")
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(.tint.opacity(0.15), in: Capsule())
                        .foregroundStyle(.tint)
                }
            }
            Text(member.email)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            if you || youLead {
                Button(role: .destructive) {
                    pendingRemoval = member
                } label: {
                    Label(you ? "Leave" : "Remove", systemImage: you ? "figure.walk.departure" : "person.badge.minus")
                }
            }
        }
    }

    private var actionsSection: some View {
        Section {
            if youLead {
                Button {
                    showRename = true
                } label: {
                    Label("Rename household…", systemImage: "pencil")
                }
                if !others.isEmpty {
                    Button {
                        showHandOver = true
                    } label: {
                        Label("Hand over the lead…", systemImage: "person.2.badge.gearshape")
                    }
                }
            }
            Button {
                showJoin = true
            } label: {
                Label("Join another household…", systemImage: "arrow.right.square")
            }
        } footer: {
            Text(
                youLead
                    ? "Handing over gives them the invites and the guest list; you stay a member and can "
                        + "leave afterwards if you want to."
                    : "An invite code from another household moves this account into it. You keep your "
                        + "account and everything signed in on it."
            )
        }
    }

    private var removalPrompt: String {
        guard let member = pendingRemoval else { return "" }
        if member.id == session.user?.id {
            return "Leave this household? You keep your account and your tokens, and land in an empty "
                + "household of your own. The recipes, plan and history stay here."
        }
        return "Remove \(member.displayName)? They keep their account and land in an empty household of "
            + "their own. Everything they added here stays."
    }

    private func refresh() async {
        do {
            household = try await session.api.household()
            loaded = true
            errorMessage = nil
        } catch let APIError.server(status, _) where status == 404 {
            // A server older than Q23 has no /auth/household. Say so plainly
            // rather than showing its 404 text, which is about a missing route
            // and means nothing to the person reading it.
            loaded = true
            errorMessage = "This server is too old to list who's in your household. Update the server to see it here."
        } catch {
            loaded = true
            errorMessage = error.localizedDescription
        }
    }

    private func remove(_ member: HouseholdMember) async {
        do {
            let result = try await session.removeMember(id: member.id)
            if result.youLeft {
                // Every cached read belongs to a household this account is no
                // longer in — the same reasoning as logging out.
                clearCaches()
            }
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func clearCaches() {
        planStore.clearCache()
        recipeStore.clearCache()
    }
}

/// Renaming is the lead's, and it is only ever a name — nothing about the
/// household's data moves with it.
private struct RenameHouseholdSheet: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    let currentName: String
    let onSaved: () async -> Void

    @State private var name = ""
    @State private var isWorking = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Household name", text: $name)
                        .textContentType(.organizationName)
                }
                if let errorMessage {
                    Section { Text(errorMessage).foregroundStyle(.red).font(.callout) }
                }
            }
            .navigationTitle("Rename household")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }
                        .disabled(isWorking || name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .onAppear { if name.isEmpty { name = currentName } }
        }
    }

    private func save() {
        isWorking = true
        errorMessage = nil
        Task {
            defer { isWorking = false }
            do {
                _ = try await session.api.updateHousehold(name: name.trimmingCharacters(in: .whitespaces))
                await session.restore()
                await onSaved()
                dismiss()
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}

/// Handing the lead on. Immediate and without asking them first: while the lead
/// only gates a guest list that is a fair trade for keeping it simple.
private struct HandOverLeadSheet: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    let members: [HouseholdMember]
    let onSaved: () async -> Void

    @State private var chosen: UUID?
    @State private var isWorking = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    ForEach(members) { member in
                        Button {
                            chosen = member.id
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(member.displayName).foregroundStyle(.primary)
                                    Text(member.email).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                if chosen == member.id {
                                    Image(systemName: "checkmark").foregroundStyle(.tint)
                                }
                            }
                        }
                    }
                } footer: {
                    Text(
                        "They get the invites and the guest list. You become an ordinary member — still "
                            + "able to change every recipe, plan and list, as everyone here is."
                    )
                }
                if let errorMessage {
                    Section { Text(errorMessage).foregroundStyle(.red).font(.callout) }
                }
            }
            .navigationTitle("Hand over the lead")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Hand over") { save() }.disabled(isWorking || chosen == nil)
                }
            }
        }
    }

    private func save() {
        guard let chosen else { return }
        isWorking = true
        errorMessage = nil
        Task {
            defer { isWorking = false }
            do {
                _ = try await session.api.updateHousehold(leadUserId: chosen)
                await session.restore()
                await onSaved()
                dismiss()
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}

/// Joining another household with a code, without deleting anything and
/// starting again. The one thing it can cost is a library nobody else is in,
/// which the server refuses to drop until it is asked twice.
private struct JoinHouseholdSheet: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    let currentName: String
    let clearCaches: () -> Void

    @State private var code = ""
    @State private var isWorking = false
    @State private var errorMessage: String?
    @State private var confirmAbandon: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("XXXX-XXXX-XXXX", text: $code)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .font(.callout.monospaced())
                } footer: {
                    Text(
                        "You keep this account and everything signed in on it — only which household "
                            + "you're in changes. “\(currentName)” keeps its recipes unless you're its "
                            + "only member, in which case they go with you."
                    )
                }
                if let errorMessage {
                    Section { Text(errorMessage).foregroundStyle(.red).font(.callout) }
                }
            }
            .navigationTitle("Join a household")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Join") { join(force: false) }
                        .disabled(isWorking || code.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .confirmationDialog(
                confirmAbandon ?? "",
                isPresented: .init(get: { confirmAbandon != nil }, set: { if !$0 { confirmAbandon = nil } }),
                titleVisibility: .visible
            ) {
                Button("Join anyway", role: .destructive) {
                    confirmAbandon = nil
                    join(force: true)
                }
            }
        }
    }

    private func join(force: Bool) {
        isWorking = true
        errorMessage = nil
        Task {
            defer { isWorking = false }
            do {
                try await session.joinHousehold(code: code.trimmingCharacters(in: .whitespaces), force: force)
                clearCaches()
                dismiss()
            } catch let APIError.server(status, detail) where status == 409 && detail.contains("force") {
                // The server is saying this would delete a library nobody else
                // can reach. Ask in those words rather than retrying quietly.
                confirmAbandon = detail
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
