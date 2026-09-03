import SwiftUI

/// The freezer: a running tab of cooked portions waiting to be eaten (Q24).
/// One row per batch, oldest first because that is the one to eat next.
/// Nothing here touches the plan or the list — eating from the freezer is not
/// a cooking; that was recorded when the batch was made.
struct FreezerView: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    /// Presented as a sheet (from the plan menu) rather than pushed — the
    /// sheet needs its own Done button.
    let embedded: Bool

    @State private var stock: FreezerPayload?
    @State private var isWorking = false
    @State private var errorMessage: String?
    @State private var showAdd = false
    @State private var pendingRemove: FreezerItem?

    var body: some View {
        List {
            if let stock, !stock.items.isEmpty {
                Section {
                    ForEach(stock.items) { item in
                        row(item)
                    }
                } header: {
                    Text("Oldest first — eat from the top")
                } footer: {
                    Text(
                        "\(stock.totalPortions) portion\(stock.totalPortions == 1 ? "" : "s") in "
                            + "\(stock.items.count) batch\(stock.items.count == 1 ? "" : "es"). "
                            + "Swipe right on a batch when you take a portion out."
                    )
                }
            }
        }
        .navigationTitle("Freezer")
        .navigationBarTitleDisplayMode(.inline)
        .overlay {
            if let stock, stock.items.isEmpty {
                ContentUnavailableView(
                    "Nothing in the freezer",
                    systemImage: "snowflake",
                    description: Text(
                        "When you batch-cook, put the spare portions here and this becomes the answer "
                            + "to “what's for tea?”."
                    )
                )
            } else if stock == nil {
                ProgressView()
            }
        }
        .toolbar {
            if embedded {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
            }
            ToolbarItem(placement: .primaryAction) {
                Button {
                    showAdd = true
                } label: {
                    Image(systemName: "plus")
                }
                .accessibilityLabel("Put something in the freezer")
            }
        }
        .task { await refresh() }
        .refreshable { await refresh() }
        .sheet(isPresented: $showAdd) {
            AddToFreezerSheet { await refresh() }
        }
        .confirmationDialog(
            removeConfirmTitle,
            isPresented: .init(get: { pendingRemove != nil }, set: { if !$0 { pendingRemove = nil } }),
            titleVisibility: .visible
        ) {
            Button("Take it out", role: .destructive) {
                guard let item = pendingRemove else { return }
                pendingRemove = nil
                Task { await remove(item) }
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

    private var removeConfirmTitle: String {
        guard let item = pendingRemove else { return "" }
        return "Take the \(item.label) out? Its \(item.portionsText) come off the tab — for something binned or given away. Eating one is the swipe."
    }

    private func row(_ item: FreezerItem) -> some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text(item.label)
                Text(subtitle(item))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text(item.portionsText)
                .font(.callout.weight(.semibold))
                .foregroundStyle(item.portions == 1 ? .orange : .primary)
        }
        .swipeActions(edge: .leading, allowsFullSwipe: true) {
            Button {
                Task { await take(item) }
            } label: {
                Label("Ate one", systemImage: "fork.knife")
            }
            .tint(.green)
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button(role: .destructive) {
                pendingRemove = item
            } label: {
                Label("Take out", systemImage: "trash")
            }
            Button {
                Task { await recount(item, to: item.portions + 1) }
            } label: {
                Label("One more", systemImage: "plus")
            }
            .tint(.blue)
        }
    }

    private func subtitle(_ item: FreezerItem) -> String {
        var parts = [item.frozenText]
        if let note = item.note, !note.isEmpty { parts.append(note) }
        if item.mealId == nil && item.recipeId == nil { parts.append("not from a recipe here") }
        return parts.joined(separator: " · ")
    }

    private func refresh() async {
        do {
            stock = try await session.api.freezer()
        } catch let error as APIError where error.isConnectivity {
            // Leave whatever was on screen; the freezer lives on the server.
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func take(_ item: FreezerItem) async {
        isWorking = true
        defer { isWorking = false }
        do {
            _ = try await session.api.takeFromFreezer(id: item.id)
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func recount(_ item: FreezerItem, to portions: Int) async {
        isWorking = true
        defer { isWorking = false }
        do {
            _ = try await session.api.updateFreezerItem(id: item.id, portions: portions)
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func remove(_ item: FreezerItem) async {
        isWorking = true
        defer { isWorking = false }
        do {
            try await session.api.removeFromFreezer(id: item.id)
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

/// Three ways to say what went in (Q24): a meal, a recipe, or free text for
/// food that never passed through the plan. Free text is the fallback, not
/// the default — a batch linked to its meal stays a tap from the recipe.
struct AddToFreezerSheet: View {
    enum Kind: String, CaseIterable, Identifiable {
        case meal = "A meal"
        case recipe = "A recipe"
        case text = "Something else"
        var id: String { rawValue }
    }

    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    /// Runs after a successful save, so the list behind refreshes.
    let onSaved: () async -> Void

    @State private var kind: Kind = .meal
    @State private var search = ""
    @State private var meals: [Meal] = []
    @State private var recipes: [RecipeSummary] = []
    @State private var pickedMeal: Meal?
    @State private var pickedRecipe: RecipeSummary?
    @State private var label = ""
    @State private var portions = 4
    @State private var frozenOn = Date.now
    @State private var note = ""
    @State private var isSaving = false
    @State private var loaded = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("What is it", selection: $kind) {
                        ForEach(Kind.allCases) { kind in
                            Text(kind.rawValue).tag(kind)
                        }
                    }
                    .pickerStyle(.segmented)
                    .onChange(of: kind) {
                        pickedMeal = nil
                        pickedRecipe = nil
                    }
                }

                switch kind {
                case .meal:
                    Section {
                        TextField("Search meals", text: $search)
                            .textInputAutocapitalization(.never)
                        ForEach(filteredMeals) { meal in
                            pickRow(name: meal.name, detail: mealDetail(meal), picked: pickedMeal?.id == meal.id) {
                                pickedMeal = meal
                            }
                        }
                        if loaded && filteredMeals.isEmpty {
                            Text("Nothing matches — try “Something else” for free text.")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                    }
                case .recipe:
                    Section {
                        TextField("Search recipes", text: $search)
                            .textInputAutocapitalization(.never)
                        ForEach(filteredRecipes) { recipe in
                            pickRow(
                                name: recipe.title,
                                detail: recipe.servings.map { "serves \($0)" },
                                picked: pickedRecipe?.id == recipe.id
                            ) {
                                pickedRecipe = recipe
                            }
                        }
                        if loaded && filteredRecipes.isEmpty {
                            Text("Nothing matches — try “Something else” for free text.")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                    }
                case .text:
                    Section {
                        TextField("What is it (e.g. Mum's lasagne)", text: $label)
                    } footer: {
                        Text("For food that didn't come through a recipe here.")
                    }
                }

                Section {
                    Stepper("Portions: \(portions)", value: $portions, in: 1...500)
                    DatePicker("Frozen on", selection: $frozenOn, in: ...Date.now, displayedComponents: .date)
                    TextField("Note (optional, e.g. the spicy batch)", text: $note)
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.red).font(.callout)
                    }
                }
            }
            .navigationTitle("Into the freezer")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Freeze") { save() }
                        .disabled(isSaving || !canSave)
                        .fontWeight(.semibold)
                }
            }
            .task {
                guard !loaded else { return }
                do {
                    meals = try await session.api.meals()
                    recipes = try await session.api.recipes(search: nil)
                    loaded = true
                } catch {
                    errorMessage = error.localizedDescription
                }
            }
        }
    }

    private var filteredMeals: [Meal] {
        let wanted = search.trimmingCharacters(in: .whitespaces).lowercased()
        return wanted.isEmpty ? meals : meals.filter { $0.name.lowercased().contains(wanted) }
    }

    private var filteredRecipes: [RecipeSummary] {
        let wanted = search.trimmingCharacters(in: .whitespaces).lowercased()
        return wanted.isEmpty ? recipes : recipes.filter { $0.title.lowercased().contains(wanted) }
    }

    private var canSave: Bool {
        switch kind {
        case .meal: pickedMeal != nil
        case .recipe: pickedRecipe != nil
        case .text: !label.trimmingCharacters(in: .whitespaces).isEmpty
        }
    }

    private func mealDetail(_ meal: Meal) -> String? {
        let titles = meal.recipes.map(\.title)
        return titles.isEmpty ? nil : titles.joined(separator: " + ")
    }

    private func pickRow(name: String, detail: String?, picked: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Image(systemName: picked ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(picked ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                VStack(alignment: .leading, spacing: 2) {
                    Text(name).foregroundStyle(.primary)
                    if let detail, !detail.isEmpty {
                        Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
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
                let formatter = DateFormatter()
                formatter.calendar = Calendar(identifier: .iso8601)
                formatter.locale = Locale(identifier: "en_US_POSIX")
                formatter.timeZone = .current
                formatter.dateFormat = "yyyy-MM-dd"
                let trimmedNote = note.trimmingCharacters(in: .whitespaces)
                _ = try await session.api.addToFreezer(
                    mealId: kind == .meal ? pickedMeal?.id : nil,
                    recipeId: kind == .recipe ? pickedRecipe?.id : nil,
                    label: kind == .text ? label.trimmingCharacters(in: .whitespaces) : nil,
                    portions: portions,
                    note: trimmedNote.isEmpty ? nil : trimmedNote,
                    frozenOn: formatter.string(from: frozenOn)
                )
                await onSaved()
                dismiss()
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
