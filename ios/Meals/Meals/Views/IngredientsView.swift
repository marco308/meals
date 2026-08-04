import SwiftUI

/// The ingredient catalogue: aisles, staples and the household's own
/// ⭐ premium / 💷 budget verdicts (Q17 — never guessed, so this is where
/// they get set). Rows push the same ingredient editor every other screen
/// uses; the duplicates sweep (Q21) lives in the toolbar. Needs the server —
/// curation is deliberate work, not a mid-shop activity, so there's no
/// offline cache here.
struct IngredientsView: View {
    @Environment(Session.self) private var session

    @State private var items: [IngredientInfo] = []
    @State private var search = ""
    @State private var staplesOnly = false
    @State private var tierFilter: ValueTier?
    @State private var sort: IngredientSort = .name
    @State private var loaded = false
    @State private var isOffline = false
    @State private var showDuplicates = false
    @State private var pendingDelete: IngredientInfo?
    @State private var actionError: String?
    /// Stale-response guard: a slow fetch must not overwrite a newer filter's
    /// results (same trick the web app's list views use).
    @State private var fetchTicket = 0

    var body: some View {
        NavigationStack {
            List {
                ForEach(sections, id: \.title) { section in
                    // A–Z runs as one unbroken list; the header only earns its
                    // place when the sort creates real groups.
                    Section {
                        ForEach(section.items) { item in
                            NavigationLink {
                                IngredientEditorView(ingredientId: item.id) {
                                    Task { await refresh() }
                                }
                            } label: {
                                row(item)
                            }
                            // No full swipe: deleting can't be undone, so it
                            // always goes through the confirmation.
                            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                Button(role: .destructive) {
                                    pendingDelete = item
                                } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                            }
                        }
                    } header: {
                        if !section.title.isEmpty {
                            Text(section.title)
                        }
                    }
                }

                if loaded && items.isEmpty {
                    if isOffline {
                        ContentUnavailableView(
                            "Offline",
                            systemImage: "wifi.slash",
                            description: Text("The catalogue needs the server — it'll be here when you're back online.")
                        )
                    } else {
                        ContentUnavailableView(
                            hasFilters ? "Nothing matches" : "Nothing here",
                            systemImage: "carrot",
                            description: Text(
                                hasFilters
                                    ? "No ingredient matches those filters."
                                    : "Ingredients appear as recipes and shopping lists use them."
                            )
                        )
                    }
                }
            }
            .navigationTitle("Ingredients")
            .searchable(text: $search, prompt: "Search ingredients")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Picker("Sort", selection: $sort) {
                            ForEach(IngredientSort.allCases, id: \.self) { option in
                                Text(option.label).tag(option)
                            }
                        }
                        Divider()
                        Toggle("Staples only", isOn: $staplesOnly)
                        Picker("Verdict", selection: $tierFilter) {
                            Text("Any verdict").tag(ValueTier?.none)
                            Text("⭐ Premium").tag(Optional(ValueTier.premium))
                            Text("💷 Budget").tag(Optional(ValueTier.budget))
                        }
                        .pickerStyle(.menu)
                    } label: {
                        Image(systemName: hasFilters
                            ? "line.3.horizontal.decrease.circle.fill"
                            : "line.3.horizontal.decrease.circle")
                    }
                    .accessibilityLabel("Sort and filter")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showDuplicates = true
                    } label: {
                        Image(systemName: "arrow.triangle.merge")
                    }
                    .accessibilityLabel("Find duplicates")
                }
            }
            .task { await refresh() }
            .refreshable { await refresh() }
            .onChange(of: search) { _, _ in Task { await refresh() } }
            .onChange(of: sort) { _, _ in Task { await refresh() } }
            .onChange(of: staplesOnly) { _, _ in Task { await refresh() } }
            .onChange(of: tierFilter) { _, _ in Task { await refresh() } }
            .sheet(isPresented: $showDuplicates) {
                DuplicatesView { Task { await refresh() } }
            }
            .confirmationDialog(
                "Delete '\(pendingDelete?.name ?? "")'? Only works if nothing references it — recipes, meals and list lines all protect their ingredients.",
                isPresented: .init(get: { pendingDelete != nil }, set: { if !$0 { pendingDelete = nil } }),
                titleVisibility: .visible
            ) {
                Button("Delete ingredient", role: .destructive) {
                    guard let item = pendingDelete else { return }
                    pendingDelete = nil
                    Task { await remove(item) }
                }
            }
            .alert(
                "Couldn't do that",
                isPresented: .init(get: { actionError != nil }, set: { if !$0 { actionError = nil } })
            ) {
                Button("OK") { actionError = nil }
            } message: {
                Text(actionError ?? "")
            }
        }
    }

    private var hasFilters: Bool { staplesOnly || tierFilter != nil }

    /// Sorted by aisle or verdict, the list reads better with the groups the
    /// sort implies; A–Z stays one flat run. Items arrive server-sorted, so
    /// grouping is just cutting the list where the label changes.
    private var sections: [(title: String, items: [IngredientInfo])] {
        switch sort {
        case .name:
            return items.isEmpty ? [] : [(title: "", items: items)]
        case .aisle:
            return grouped { "\($0.aisle) \($0.aisleLabel)" }
        case .valueTier:
            return grouped { $0.tier == .any ? "No opinion" : "\($0.tier.badge) \($0.tier.short)" }
        }
    }

    private func grouped(_ label: (IngredientInfo) -> String) -> [(title: String, items: [IngredientInfo])] {
        var result: [(title: String, items: [IngredientInfo])] = []
        for item in items {
            let title = label(item)
            if result.last?.title == title {
                result[result.count - 1].items.append(item)
            } else {
                result.append((title: title, items: [item]))
            }
        }
        return result
    }

    private func row(_ item: IngredientInfo) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(item.aisle)
                Text(item.name)
                if !item.tier.badge.isEmpty {
                    Text(item.tier.badge)
                        .font(.caption2)
                        .accessibilityLabel(item.tier.short)
                }
                if item.isStaple {
                    Image(systemName: "cabinet")
                        .imageScale(.small)
                        .foregroundStyle(.secondary)
                        .accessibilityLabel("Staple")
                }
            }
            if let note = item.valueNote, !note.isEmpty {
                Text(note)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
    }

    private func refresh() async {
        fetchTicket += 1
        let ticket = fetchTicket
        do {
            let fetched = try await session.api.ingredients(
                search: search.trimmingCharacters(in: .whitespaces),
                staplesOnly: staplesOnly,
                valueTier: tierFilter,
                sort: sort
            )
            guard ticket == fetchTicket else { return }  // a newer filter overtook this fetch
            items = fetched
            loaded = true
            isOffline = false
        } catch let error as APIError where error.isConnectivity {
            guard ticket == fetchTicket else { return }
            loaded = true
            isOffline = true
        } catch {
            guard ticket == fetchTicket else { return }
            actionError = error.localizedDescription
        }
    }

    private func remove(_ item: IngredientInfo) async {
        do {
            try await session.api.deleteIngredient(id: item.id)
            await refresh()
        } catch {
            // The 409 names every reference and the way out — show it verbatim.
            actionError = error.localizedDescription
        }
    }
}

/// One line of curation context, so a merge choice is made knowing whose
/// aisle/staple/verdict survives (the keeper's) and whose is lost.
private func ingredientMeta(_ item: IngredientInfo) -> String {
    var parts = ["\(item.aisle) \(item.aisleLabel)"]
    if item.isStaple { parts.append("staple") }
    if item.tier != .any { parts.append("\(item.tier.badge) \(item.tier.short.lowercased())") }
    if let note = item.valueNote, !note.isEmpty { parts.append("\u{201C}\(note)\u{201D}") }
    return parts.joined(separator: " · ")
}

/// Q21's clean-up screen: same food, different spellings. Groups are
/// name-folding facts the server found; each merge repoints every recipe
/// line and list item at the chosen keeper, then removes the rest.
struct DuplicatesView: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    /// Called after any successful merge, so the catalogue behind refreshes.
    let onChanged: () -> Void

    @State private var payload: DuplicatesPayload?
    @State private var errorMessage: String?
    /// Chosen keeper per group index; the server's suggestion until a tap.
    @State private var keepers: [Int: UUID] = [:]
    @State private var isWorking = false

    var body: some View {
        NavigationStack {
            List {
                if let payload {
                    if payload.groups.isEmpty && payload.unfolded.isEmpty {
                        ContentUnavailableView(
                            "Catalogue is clean",
                            systemImage: "sparkles",
                            description: Text(
                                "No duplicate names found. Spotted a pair this can't see, like "
                                    + "'beef mince' and 'minced beef'? Open the ingredient and merge it from there."
                            )
                        )
                    }

                    ForEach(Array(payload.groups.enumerated()), id: \.offset) { index, group in
                        groupSection(index: index, group: group)
                    }

                    if !payload.unfolded.isEmpty {
                        unfoldedSection(payload.unfolded)
                    }
                } else if let errorMessage {
                    ContentUnavailableView(
                        "Couldn't load duplicates",
                        systemImage: "exclamationmark.triangle",
                        description: Text(errorMessage)
                    )
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Duplicates")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .task { await load() }
            .alert(
                "Couldn't merge",
                isPresented: .init(
                    get: { errorMessage != nil && payload != nil },
                    set: { if !$0 { errorMessage = nil } }
                )
            ) {
                Button("OK") { errorMessage = nil }
            } message: {
                Text(errorMessage ?? "")
            }
        }
    }

    private func groupSection(index: Int, group: DuplicateGroup) -> some View {
        let members = [group.keeper] + group.duplicates
        let keeperId = keepers[index] ?? group.keeper.id
        let keeper = members.first { $0.id == keeperId } ?? group.keeper
        return Section {
            ForEach(members) { member in
                Button {
                    keepers[index] = member.id
                } label: {
                    HStack {
                        Image(systemName: member.id == keeperId ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(member.id == keeperId ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 6) {
                                Text(member.name).foregroundStyle(.primary)
                                if member.id == group.keeper.id {
                                    Text("suggested")
                                        .font(.caption2)
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 1)
                                        .background(.quaternary, in: Capsule())
                                }
                            }
                            Text(ingredientMeta(member))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .disabled(isWorking)
            }
            Button {
                Task { await merge(group: group, keeper: keeper) }
            } label: {
                Text("Merge \(members.count - 1) into '\(keeper.name)'")
                    .fontWeight(.medium)
            }
            .disabled(isWorking)
        } footer: {
            if keeper.name != group.canonicalName {
                Text(
                    "New recipes file this food under '\(group.canonicalName)', "
                        + "so a separate '\(group.canonicalName)' can creep back."
                )
            } else {
                Text("The survivor keeps its own aisle, staple flag and verdict.")
            }
        }
    }

    private func unfoldedSection(_ unfolded: [UnfoldedIngredient]) -> some View {
        Section {
            ForEach(Array(unfolded.enumerated()), id: \.offset) { _, entry in
                VStack(alignment: .leading, spacing: 4) {
                    Text(entry.ingredient.name)
                    Text(ingredientMeta(entry.ingredient))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Button("File under '\(entry.canonicalName)'") {
                        Task { await file(entry) }
                    }
                    .buttonStyle(.bordered)
                    .font(.callout)
                    .disabled(isWorking)
                }
                .padding(.vertical, 2)
            }
        } header: {
            Text("Old spellings")
        } footer: {
            Text(
                "Stored under a name a new recipe wouldn't use, with nothing to merge into. "
                    + "Filing one moves it to the modern name, keeping its aisle, staple flag and verdict."
            )
        }
    }

    private func load() async {
        do {
            payload = try await session.api.ingredientDuplicates()
            keepers = [:]
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func merge(group: DuplicateGroup, keeper: IngredientInfo) async {
        isWorking = true
        defer { isWorking = false }
        let members = [group.keeper] + group.duplicates
        let duplicateIds = members.filter { $0.id != keeper.id }.map(\.id)
        do {
            _ = try await session.api.mergeIngredients(keeperId: keeper.id, duplicateIds: duplicateIds)
            await load()
            onChanged()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// The canonical row doesn't exist yet (that's what made this "unfolded"),
    /// so create it carrying the old row's curation — this is a rename, not a
    /// merge of two opinions — then fold the old row into it.
    private func file(_ entry: UnfoldedIngredient) async {
        isWorking = true
        defer { isWorking = false }
        do {
            let keeper = try await session.api.createIngredient(
                name: entry.canonicalName,
                aisle: entry.ingredient.aisle,
                isStaple: entry.ingredient.isStaple,
                valueTier: entry.ingredient.tier,
                valueNote: entry.ingredient.valueNote
            )
            _ = try await session.api.mergeIngredients(keeperId: keeper.id, duplicateIds: [entry.ingredient.id])
            await load()
            onChanged()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

/// Manual merge for the pairs the duplicates finder deliberately won't claim
/// (Q21: it reports name-folding facts, not guesses) — "beef mince" vs
/// "minced beef". The human supplies the judgement; the ingredient they pick
/// survives with its own name and curation, and the source is deleted.
struct MergeIntoSheet: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    let source: IngredientInfo
    /// Called once the source has been folded away — the presenter closes
    /// itself, because the ingredient it was showing no longer exists.
    let onMerged: () -> Void

    @State private var search = ""
    @State private var results: [IngredientInfo] = []
    @State private var pendingTarget: IngredientInfo?
    @State private var isWorking = false
    @State private var errorMessage: String?
    @State private var loaded = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text(
                        "Everything pointing at '\(source.name)' moves onto the ingredient you pick, "
                            + "then '\(source.name)' is deleted. Not reversible — merge spellings of the "
                            + "same food, not things bought together."
                    )
                    .font(.callout)
                    .foregroundStyle(.secondary)
                }

                ForEach(results) { item in
                    Button {
                        pendingTarget = item
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.name).foregroundStyle(.primary)
                            Text(ingredientMeta(item))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .disabled(isWorking)
                }

                if loaded && results.isEmpty {
                    Text("No other ingredient matches.")
                        .foregroundStyle(.secondary)
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.red).font(.callout)
                    }
                }
            }
            .navigationTitle("Merge '\(source.name)' into…")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $search, prompt: "Search ingredients")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .task { await load() }
            .onChange(of: search) { _, _ in Task { await load() } }
            .confirmationDialog(
                "Merge '\(source.name)' into '\(pendingTarget?.name ?? "")'? This can't be undone.",
                isPresented: .init(get: { pendingTarget != nil }, set: { if !$0 { pendingTarget = nil } }),
                titleVisibility: .visible
            ) {
                Button("Merge", role: .destructive) {
                    guard let target = pendingTarget else { return }
                    pendingTarget = nil
                    Task { await merge(into: target) }
                }
            }
        }
    }

    private func load() async {
        do {
            let items = try await session.api.ingredients(search: search.trimmingCharacters(in: .whitespaces))
            results = items.filter { $0.id != source.id }
            loaded = true
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func merge(into target: IngredientInfo) async {
        isWorking = true
        defer { isWorking = false }
        do {
            _ = try await session.api.mergeIngredients(keeperId: target.id, duplicateIds: [source.id])
            dismiss()
            onMerged()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
