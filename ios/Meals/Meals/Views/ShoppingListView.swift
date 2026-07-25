import SwiftUI

/// The aisle-sorted shopping list. Check-offs and quick adds work with no
/// signal: every interaction is applied locally and queued, then synced.
struct ShoppingListView: View {
    @Environment(ShoppingListStore.self) private var store
    @State private var quickAddText = ""
    @State private var showStaplesCheck = false
    @State private var showFinishConfirm = false
    @State private var finishError: String?

    var body: some View {
        @Bindable var store = store
        NavigationStack {
            List {
                if store.isOffline {
                    Section {
                        Label("Offline — changes will sync when you're back", systemImage: "wifi.slash")
                            .font(.callout)
                            .foregroundStyle(.orange)
                    }
                }

                Section {
                    HStack {
                        TextField("Add something (e.g. milk)", text: $quickAddText)
                            .onSubmit(quickAdd)
                        Button(action: quickAdd) {
                            Image(systemName: "plus.circle.fill")
                        }
                        .disabled(quickAddText.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }

                ForEach(store.sections, id: \.aisle) { section in
                    Section("\(section.aisle) \(section.label)") {
                        ForEach(section.items) { item in
                            ShoppingItemRow(item: item)
                        }
                    }
                }

                if store.displayItems.isEmpty {
                    ContentUnavailableView(
                        "Nothing to buy",
                        systemImage: "cart",
                        description: Text(emptyHint)
                    )
                }

                if !store.stapleCheckItems.isEmpty && !store.includeStaples {
                    Section {
                        Button {
                            showStaplesCheck = true
                        } label: {
                            Label("Check staples (\(store.stapleCheckItems.count))", systemImage: "cabinet")
                                .font(.callout)
                        }
                    }
                }
            }
            .navigationTitle("Shopping")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Toggle("Show staples", isOn: $store.includeStaples)
                        Toggle("Show checked-off", isOn: $store.includeChecked)
                        Toggle("Show 'already have' (\(store.excludedCount))", isOn: $store.includeExcluded)
                        Button("Finish shop", systemImage: "checkmark.seal") {
                            showFinishConfirm = true
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
            .task { await store.sync() }
            .refreshable { await store.sync() }
            .sheet(isPresented: $showStaplesCheck) {
                StaplesCheckView()
            }
            .confirmationDialog(
                "Archive this list and start fresh?",
                isPresented: $showFinishConfirm,
                titleVisibility: .visible
            ) {
                Button("Finish shop", role: .destructive) {
                    Task {
                        do {
                            try await store.finishShop()
                        } catch {
                            finishError = error.localizedDescription
                        }
                    }
                }
            }
            .alert("Couldn't finish the shop", isPresented: .init(get: { finishError != nil }, set: { if !$0 { finishError = nil } })) {
                Button("OK") { finishError = nil }
            } message: {
                Text(finishError ?? "")
            }
        }
    }

    private var emptyHint: String {
        if store.cache == nil {
            return "Pull to refresh once you're online — after that the list works offline."
        }
        return "Add meals to the plan or quick-add items above."
    }

    private func quickAdd() {
        let text = quickAddText.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return }
        let (name, quantity, unit) = Self.parseQuickAdd(text)
        store.addAdhoc(name: name, quantity: quantity, unit: unit)
        quickAddText = ""
    }

    /// "milk 2 l" / "2 l milk" / "milk 2l" / "bin bags" → (name, qty?, unit?).
    /// Shared with the meal editor's side entry — see `QuantityParser`, which
    /// is where the glued-unit handling lives (#30).
    static func parseQuickAdd(_ text: String) -> (String, Double?, String?) {
        QuantityParser.parse(text)
    }
}

struct ShoppingItemRow: View {
    @Environment(ShoppingListStore.self) private var store
    let item: ListItem
    @State private var showDetail = false

    var body: some View {
        Button {
            store.toggleChecked(item)
        } label: {
            HStack {
                Image(systemName: item.checked ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(item.checked ? .green : .secondary)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 4) {
                        Text(item.name)
                            .strikethrough(item.checked, color: .secondary)
                            .foregroundStyle(item.checked || item.excluded ? .secondary : .primary)
                        if item.excluded {
                            Image(systemName: "house.fill")
                                .font(.caption2)
                                .foregroundStyle(.indigo)
                        }
                        if !item.tier.badge.isEmpty {
                            Text(item.tier.badge).font(.caption2)  // ⭐ buy the good one · 💷 own-brand
                        }
                    }
                    if !item.neededBy.isEmpty {
                        Text("for \(item.neededBy.joined(separator: ", "))")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Text(item.display)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                Button {
                    showDetail = true
                } label: {
                    Image(systemName: "info.circle")
                        .foregroundStyle(.tint)
                }
                .buttonStyle(.borderless)  // keep the row tap = check-off
            }
        }
        .buttonStyle(.plain)
        .swipeActions(edge: .trailing) {
            Button {
                store.markAlreadyHave(item)
            } label: {
                Label("Have it", systemImage: "house")
            }
            .tint(.indigo)
        }
        .sheet(isPresented: $showDetail) {
            ItemDetailSheet(item: item)
                .presentationDetents([.medium, .large])
        }
    }
}

/// The pre-shop staples check: just the staples, nothing else. Walk the
/// kitchen ticking through them — one tap puts a staple you're low on onto
/// the main list in its aisle; the rest stay hidden as usual.
struct StaplesCheckView: View {
    @Environment(ShoppingListStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if !store.stapleCheckItems.isEmpty {
                    Section {
                        Text("Anything you're low on, tap to add to this shop. The rest stay off the list.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                }

                ForEach(store.stapleCheckSections, id: \.aisle) { section in
                    Section("\(section.aisle) \(section.label)") {
                        ForEach(section.items) { item in
                            StapleCheckRow(item: item)
                        }
                    }
                }

                if store.stapleCheckItems.isEmpty {
                    ContentUnavailableView(
                        "No staples to check",
                        systemImage: "cabinet",
                        description: Text("Mark ingredients as staples and they'll wait here instead of cluttering the list.")
                    )
                }
            }
            .navigationTitle("Staples check")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}

struct StapleCheckRow: View {
    @Environment(ShoppingListStore.self) private var store
    let item: ListItem

    var body: some View {
        Button {
            if item.isNeededStaple {
                store.unmarkStapleNeeded(item)
            } else {
                store.markStapleNeeded(item)
            }
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.name)
                    if item.isNeededStaple {
                        Text("on the list — tap if you have it after all")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    } else if !item.neededBy.isEmpty {
                        Text("for \(item.neededBy.joined(separator: ", "))")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Text(item.display)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                Image(systemName: item.isNeededStaple ? "checkmark.circle.fill" : "cart.badge.plus")
                    .foregroundStyle(item.isNeededStaple ? AnyShapeStyle(.green) : AnyShapeStyle(.tint))
            }
        }
        .buttonStyle(.plain)
    }
}

/// Why is this on the list? Every contribution with its amount, linking
/// through to the recipes that need it (guiding principle 3).
struct ItemDetailSheet: View {
    @Environment(ShoppingListStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let item: ListItem

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack {
                        Text("\(item.aisle) \(item.aisleLabel)")
                        Spacer()
                        Text(item.display)
                            .foregroundStyle(.secondary)
                            .monospacedDigit()
                    }
                    if item.tier != .any || item.valueNote != nil {
                        VStack(alignment: .leading, spacing: 2) {
                            Label(
                                "\(item.tier.badge) \(item.tier.label)".trimmingCharacters(in: .whitespaces),
                                systemImage: "sterlingsign.circle"
                            )
                            if let note = item.valueNote, !note.isEmpty {
                                Text(note).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                    NavigationLink {
                        IngredientEditorView(ingredientId: item.ingredientId)
                    } label: {
                        HStack {
                            Label("Edit aisle, staple & value", systemImage: "tag")
                            if item.isStaple {
                                Spacer()
                                Text("staple").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                Section("Needed by") {
                    ForEach(Array(item.sources.enumerated()), id: \.offset) { _, source in
                        sourceRow(source)
                    }
                    if item.sources.isEmpty {
                        Text("Nothing — added by hand.")
                            .foregroundStyle(.secondary)
                    }
                }

                Section {
                    Button {
                        store.toggleChecked(item)
                        dismiss()
                    } label: {
                        Label(item.checked ? "Un-check" : "Check off", systemImage: item.checked ? "circle" : "checkmark.circle")
                    }
                    if item.excluded {
                        Button {
                            store.putBack(item)
                            dismiss()
                        } label: {
                            Label("Put back on the list", systemImage: "arrow.uturn.backward")
                        }
                    } else {
                        Button {
                            store.markAlreadyHave(item)
                            dismiss()
                        } label: {
                            Label("Already have it", systemImage: "house")
                        }
                    }
                    if item.isNeededStaple {
                        Button {
                            store.unmarkStapleNeeded(item)
                            dismiss()
                        } label: {
                            Label("Have it after all — hide staple", systemImage: "cabinet")
                        }
                    }
                }
            }
            .navigationTitle(item.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    @ViewBuilder
    private func sourceRow(_ source: ItemSource) -> some View {
        let amount = ShoppingListStore.displayQuantity(source.quantity, item.unit)
        if source.adHoc {
            HStack {
                Label("Added by hand", systemImage: "plus.circle")
                Spacer()
                Text(amount).foregroundStyle(.secondary).monospacedDigit()
            }
        } else if let recipeId = source.recipeId {
            NavigationLink {
                RecipeDetailView(recipeId: recipeId)
            } label: {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(source.recipeTitle ?? "Recipe")
                        if let meal = source.mealName {
                            Text("in \(meal)").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    Text(amount).foregroundStyle(.secondary).monospacedDigit()
                }
            }
        } else {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(source.mealName ?? "A meal")
                    Text("on the side").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Text(amount).foregroundStyle(.secondary).monospacedDigit()
            }
        }
    }
}
