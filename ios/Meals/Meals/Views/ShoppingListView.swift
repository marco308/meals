import SwiftUI

/// The aisle-sorted shopping list. Check-offs and quick adds work with no
/// signal: every interaction is applied locally and queued, then synced.
struct ShoppingListView: View {
    @Environment(ShoppingListStore.self) private var store
    @State private var quickAddText = ""
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

                if store.hiddenStaplesCount > 0 && !store.includeStaples {
                    Section {
                        Button {
                            store.includeStaples = true
                        } label: {
                            Label("Staples check (\(store.hiddenStaplesCount) hidden)", systemImage: "eye")
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

    /// "milk 2 l" / "2 l milk" / "bin bags" → (name, qty?, unit?). Kept
    /// deliberately simple: a trailing or leading "<number> <word>" pair.
    static func parseQuickAdd(_ text: String) -> (String, Double?, String?) {
        let words = text.split(separator: " ").map(String.init)
        if words.count >= 3, let quantity = Double(words[words.count - 2]) {
            let unit = words[words.count - 1]
            return (words.dropLast(2).joined(separator: " "), quantity, unit)
        }
        if words.count >= 3, let quantity = Double(words[0]) {
            return (words.dropFirst(2).joined(separator: " "), quantity, words[1])
        }
        return (text, nil, nil)
    }
}

struct ShoppingItemRow: View {
    @Environment(ShoppingListStore.self) private var store
    let item: ListItem

    var body: some View {
        Button {
            store.toggleChecked(item)
        } label: {
            HStack {
                Image(systemName: item.checked ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(item.checked ? .green : .secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.name)
                        .strikethrough(item.checked, color: .secondary)
                        .foregroundStyle(item.checked ? .secondary : .primary)
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
    }
}
