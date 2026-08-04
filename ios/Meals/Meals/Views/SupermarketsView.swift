import SwiftUI

/// The household's saved stores and their aisle walking orders — the settings
/// end of `/supermarkets`. The active store's order is what the shopping list
/// walks and what `GET /aisles` returns, on every device at once; picking
/// "Default order" goes back to the built-in walk.
struct SupermarketsView: View {
    @Environment(Session.self) private var session
    @Environment(ShoppingListStore.self) private var listStore

    @State private var markets: [Supermarket] = []
    @State private var loaded = false
    @State private var isWorking = false
    @State private var errorMessage: String?
    @State private var showAdd = false
    @State private var newName = ""
    @State private var editing: Supermarket?
    @State private var pendingDelete: Supermarket?

    var body: some View {
        List {
            Section {
                pickRow(
                    name: "Default order",
                    detail: "the built-in walk, fruit & veg first",
                    isActive: !markets.contains(where: \.isActive)
                ) {
                    await activate(nil)
                }
                ForEach(markets) { market in
                    marketRow(market)
                }
            } header: {
                Text("Shopping at")
            } footer: {
                Text(
                    "The shopping list walks the aisles in the ticked store's order — every "
                        + "device (and your AI) sorts for it. Tap a store's aisles to arrange its walk."
                )
            }

            Section {
                Button {
                    newName = ""
                    showAdd = true
                } label: {
                    Label("Add a supermarket", systemImage: "plus")
                }
                .disabled(isWorking)
            }
        }
        .navigationTitle("Supermarkets")
        .navigationBarTitleDisplayMode(.inline)
        .overlay {
            if loaded && markets.isEmpty {
                ContentUnavailableView(
                    "No stores saved",
                    systemImage: "storefront",
                    description: Text(
                        "The list uses the built-in aisle order until you save the stores "
                            + "you actually shop at and arrange their walks."
                    )
                )
                .allowsHitTesting(false)  // the Add button underneath must stay tappable
            }
        }
        .task { await refresh() }
        .refreshable { await refresh() }
        .alert("Add a supermarket", isPresented: $showAdd) {
            TextField("Name (e.g. Big Tesco)", text: $newName)
            Button("Cancel", role: .cancel) {}
            Button("Add") { add() }
        } message: {
            Text("It starts on the default aisle order — you'll arrange its own walk next.")
        }
        .sheet(item: $editing) { market in
            SupermarketEditorSheet(market: market) {
                await refresh()
                await listStore.sync()  // the active store's order may have changed
            }
        }
        .confirmationDialog(
            deleteConfirmTitle,
            isPresented: .init(get: { pendingDelete != nil }, set: { if !$0 { pendingDelete = nil } }),
            titleVisibility: .visible
        ) {
            Button("Delete supermarket", role: .destructive) {
                guard let market = pendingDelete else { return }
                pendingDelete = nil
                Task { await remove(market) }
            }
        }
        .alert(
            "Something went wrong",
            isPresented: .init(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })
        ) {
            Button("OK") { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
    }

    private var deleteConfirmTitle: String {
        guard let market = pendingDelete else { return "" }
        return market.isActive
            ? "Delete '\(market.name)'? Its saved aisle order goes with it and the list goes back to the default order."
            : "Delete '\(market.name)'? Its saved aisle order goes with it."
    }

    private func pickRow(
        name: String, detail: String, isActive: Bool, action: @escaping () async -> Void
    ) -> some View {
        Button {
            guard !isActive else { return }
            Task { await action() }
        } label: {
            HStack {
                Image(systemName: isActive ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isActive ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                VStack(alignment: .leading, spacing: 2) {
                    Text(name).foregroundStyle(.primary)
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
        }
        .disabled(isWorking)
    }

    private func marketRow(_ market: Supermarket) -> some View {
        HStack {
            pickRow(
                name: market.name,
                detail: market.aisleOrder.joined(separator: " "),
                isActive: market.isActive
            ) {
                await activate(market)
            }
            Spacer()
            Button {
                editing = market
            } label: {
                Image(systemName: "pencil.circle")
                    .foregroundStyle(.tint)
            }
            .buttonStyle(.borderless)  // keep the row tap = make active
            .accessibilityLabel("Edit \(market.name)")
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button(role: .destructive) {
                pendingDelete = market
            } label: {
                Label("Delete", systemImage: "trash")
            }
        }
    }

    private func refresh() async {
        do {
            markets = try await session.api.supermarkets()
            loaded = true
        } catch let error as APIError where error.isConnectivity {
            // Leave whatever was on screen; managing stores needs the server.
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// `nil` = back to the default order (deactivate whichever store is active).
    private func activate(_ market: Supermarket?) async {
        isWorking = true
        defer { isWorking = false }
        do {
            if let market {
                _ = try await session.api.updateSupermarket(id: market.id, isActive: true)
            } else if let active = markets.first(where: \.isActive) {
                _ = try await session.api.updateSupermarket(id: active.id, isActive: false)
            }
            await refresh()
            await listStore.sync()  // re-sort the list for the new walk
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func add() {
        let name = newName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        isWorking = true
        Task {
            defer { isWorking = false }
            do {
                let market = try await session.api.createSupermarket(name: name)
                await refresh()
                editing = market  // straight into arranging the walk
            } catch {
                errorMessage = error.localizedDescription  // e.g. the duplicate-name 409
            }
        }
    }

    private func remove(_ market: Supermarket) async {
        isWorking = true
        defer { isWorking = false }
        do {
            try await session.api.deleteSupermarket(id: market.id)
            await refresh()
            await listStore.sync()  // an active store's deletion changes the sort
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

/// One store's name and aisle walk. Drag the aisles into the order you meet
/// them; nothing is sent until Save, so Cancel really cancels.
struct SupermarketEditorSheet: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    let market: Supermarket
    /// Runs after a successful save, so the list behind refreshes.
    let onSaved: () async -> Void

    @State private var name = ""
    @State private var order: [String] = []
    @State private var labels: [String: String] = [:]
    @State private var isSaving = false
    @State private var errorMessage: String?
    @State private var loaded = false

    var body: some View {
        NavigationStack {
            List {
                Section("Name") {
                    TextField("Name", text: $name)
                }

                Section {
                    ForEach(order, id: \.self) { emoji in
                        HStack {
                            Text(emoji)
                            Text(labels[emoji] ?? "Unknown")
                        }
                    }
                    .onMove { from, to in
                        order.move(fromOffsets: from, toOffset: to)
                    }
                } header: {
                    Text("Aisle order")
                } footer: {
                    Text("First aisle you meet at the top; the shopping list walks it top to bottom.")
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.red).font(.callout)
                    }
                }
            }
            // Always-on edit mode: the drag handles are the point of the screen.
            // Rows outside the ForEach (the name field) stay interactive.
            .environment(\.editMode, .constant(.active))
            .navigationTitle(market.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }
                        .disabled(isSaving || name.trimmingCharacters(in: .whitespaces).isEmpty)
                        .fontWeight(.semibold)
                }
            }
            .task {
                guard !loaded else { return }
                loaded = true
                name = market.name
                order = market.aisleOrder
                // Labels only — the vocabulary is fixed, the order is ours to edit.
                if let aisles = try? await session.api.fetchAisles() {
                    labels = Dictionary(uniqueKeysWithValues: aisles.map { ($0.emoji, $0.label) })
                }
            }
        }
    }

    private func save() {
        isSaving = true
        errorMessage = nil
        Task {
            defer { isSaving = false }
            do {
                let trimmed = name.trimmingCharacters(in: .whitespaces)
                _ = try await session.api.updateSupermarket(
                    id: market.id,
                    name: trimmed == market.name ? nil : trimmed,
                    aisleOrder: order
                )
                await onSaved()
                dismiss()
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
