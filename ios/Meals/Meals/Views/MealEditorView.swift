import SwiftUI

/// One form for both creating a meal (F2: "picking meals from the library, or
/// creating new ones inline") and editing an existing one (issue #16). A meal =
/// a name, a slot, any recipes, and loose sides — "cottage pie with peas and
/// carrots" needs no carrot recipe.
///
/// Editing is a real PATCH, not delete-and-recreate: recreating would lose the
/// meal's place on the plan and churn the shopping list. The server re-syncs
/// the active list, so this refreshes the list store on save — seeing the
/// recalculated list is the point of the edit.
struct MealEditorView: View {
    enum Mode: Equatable {
        case create
        case edit(Meal)

        var meal: Meal? {
            if case .edit(let meal) = self { return meal }
            return nil
        }
    }

    @Environment(PlanStore.self) private var planStore
    @Environment(RecipeStore.self) private var recipeStore
    @Environment(ShoppingListStore.self) private var listStore

    var mode: Mode = .create
    /// Called with the saved meal so the presenter can add-to-plan and dismiss.
    let onSaved: (Meal) -> Void
    /// Supplied by a presenter that shows this in a sheet: the editor owns
    /// Cancel so it can warn before throwing away unsaved edits (#31).
    var onCancel: (() -> Void)? = nil

    @State private var name = ""
    @State private var slot = "dinner"
    @State private var selectedRecipes: Set<UUID> = []
    @State private var looseLines: [LooseLine] = []
    @State private var looseEntry = ""
    @State private var isSaving = false
    @State private var errorMessage: String?
    @State private var loaded = false
    /// Per-recipe batch-cooking multiple (#32), keyed by recipe id.
    @State private var scales: [UUID: Double] = [:]
    @State private var editingLine: LooseLine?
    @State private var addingLine = false
    @State private var showDiscardConfirm = false
    /// What was loaded, to tell a real edit from an untouched form.
    @State private var baseline: Snapshot?
    @FocusState private var sideFieldFocused: Bool

    /// Everything the save button would send, in a comparable shape.
    private struct Snapshot: Equatable {
        var name: String
        var slot: String
        var recipes: Set<UUID>
        var scales: [UUID: Double]
        var lines: [String]
    }

    private var snapshot: Snapshot {
        Snapshot(
            name: name,
            slot: slot,
            recipes: selectedRecipes,
            scales: scales.filter { selectedRecipes.contains($0.key) },
            lines: looseLines.map { line in
                let amount = line.quantity.map { "\($0)" } ?? ""
                return "\(line.name)|\(amount)|\(line.unit ?? "")"
            }
        )
    }

    private var hasUnsavedChanges: Bool {
        guard let baseline else { return false }
        return snapshot != baseline
    }

    private let slots = ["dinner", "lunch", "breakfast", "other"]

    private var isEditing: Bool { mode.meal != nil }

    /// What the meal will be named on save: the typed name, or the selected
    /// recipes' titles when the field is left empty (#3 — a recipe-only meal
    /// shouldn't force re-typing a name the recipe already has).
    private var resolvedName: String {
        Self.resolvedName(typed: name, selectedRecipes: selectedRecipes, library: recipeStore.recipes)
    }

    /// The recipe-derived fallback name, surfaced as the name field's
    /// placeholder so the default is visible before saving.
    private var namePlaceholder: String {
        let fallback = Self.resolvedName(typed: "", selectedRecipes: selectedRecipes, library: recipeStore.recipes)
        return fallback.isEmpty ? "Meal name (e.g. Cottage pie with peas)" : fallback
    }

    var body: some View {
        Form {
            Section {
                TextField(namePlaceholder, text: $name)
                Picker("Slot", selection: $slot) {
                    ForEach(slots, id: \.self) { Text($0.capitalized) }
                }
            }

            Section {
                if recipeStore.recipes.isEmpty {
                    Text(
                        recipeStore.isOffline
                            ? "Can't reach the server, and no saved copy of the library yet."
                            : "No recipes in the library yet — a meal can be just a name and sides."
                    )
                    .font(.callout)
                    .foregroundStyle(.secondary)
                }
                ForEach(recipeStore.recipes) { recipe in
                    VStack(alignment: .leading, spacing: 4) {
                        Button {
                            if selectedRecipes.contains(recipe.id) {
                                selectedRecipes.remove(recipe.id)
                                scales[recipe.id] = nil
                            } else {
                                selectedRecipes.insert(recipe.id)
                            }
                        } label: {
                            HStack {
                                Text(recipe.title).foregroundStyle(.primary)
                                Spacer()
                                if selectedRecipes.contains(recipe.id) {
                                    Image(systemName: "checkmark").foregroundStyle(.tint)
                                }
                            }
                        }
                        // Batch cooking (#32): the multiple belongs to this
                        // meal, so the recipe and any other meal using it are
                        // untouched. Asked in portions when the recipe says how
                        // many it serves, because that's how it comes up
                        // ("there'll be six of us"), and in multiples when it
                        // doesn't (#53).
                        if selectedRecipes.contains(recipe.id) {
                            if let servings = recipe.servings {
                                Stepper(value: servingsBinding(recipe.id, servings), in: 1...100, step: 1) {
                                    Text(scaleLabel(recipe))
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            } else {
                                Stepper(value: scaleBinding(recipe.id), in: 0.5...20, step: 0.5) {
                                    Text(scaleLabel(recipe))
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
            } header: {
                Text("Recipes")
            } footer: {
                if selectedRecipes.contains(where: { (scales[$0] ?? 1) != 1 }) {
                    Text("Scaling changes what this meal puts on the shopping list. The recipe itself doesn't change.")
                }
            }

            Section {
                ForEach($looseLines) { $line in
                    EditableLineRow(
                        line: line,
                        onEdit: { editingLine = line },
                        onDelete: { looseLines.removeAll { $0.id == line.id } }
                    )
                }
                .onDelete { looseLines.remove(atOffsets: $0) }
                HStack {
                    TextField("Add a side (e.g. frozen peas 200 g)", text: $looseEntry)
                        .focused($sideFieldFocused)
                        .onSubmit(addLooseLine)
                    Button(action: addLooseLine) {
                        Image(systemName: "plus.circle.fill")
                    }
                    .disabled(looseEntry.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                Button {
                    addingLine = true
                } label: {
                    Label("Add with an amount", systemImage: "plus.circle")
                }
            } header: {
                Text("On the side")
            } footer: {
                Text("Loose ingredients go straight on the shopping list with the meal — no recipe needed. Tap one to change its amount.")
            }

            if let errorMessage {
                Section {
                    Text(errorMessage).foregroundStyle(.red).font(.callout)
                }
            }

            Section {
                Button(action: save) {
                    if isSaving {
                        ProgressView().frame(maxWidth: .infinity)
                    } else {
                        Text(isEditing ? "Save changes" : "Create and add to plan")
                            .frame(maxWidth: .infinity)
                            .fontWeight(.semibold)
                    }
                }
                .disabled(isSaving || resolvedName.isEmpty)
            } footer: {
                if isEditing {
                    Text("The shopping list follows the change: new ingredients appear, removed ones come off. Things you've already ticked off stay ticked.")
                }
            }
        }
        .navigationTitle(isEditing ? "Edit meal" : "New meal")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                // Also at the top: swiping a side away then dismissing from up
                // here used to discard the change silently (#31).
                Button("Save") { save() }
                    .disabled(isSaving || resolvedName.isEmpty)
                    .fontWeight(.semibold)
            }
            if let onCancel {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        if hasUnsavedChanges { showDiscardConfirm = true } else { onCancel() }
                    }
                }
            }
        }
        .interactiveDismissDisabled(hasUnsavedChanges)
        .confirmationDialog(
            "Discard your changes to this meal?",
            isPresented: $showDiscardConfirm,
            titleVisibility: .visible
        ) {
            Button("Discard changes", role: .destructive) { onCancel?() }
            Button("Keep editing", role: .cancel) {}
        }
        // Editing a side is a nested edit of unsaved state — it must not
        // commit anything on its own.
        .sheet(item: $editingLine) { line in
            NavigationStack {
                IngredientLineEditor(line: line, title: "Edit side") { updated in
                    guard let index = looseLines.firstIndex(where: { $0.id == line.id }) else { return }
                    looseLines[index] = updated
                }
            }
        }
        .sheet(isPresented: $addingLine) {
            NavigationStack {
                IngredientLineEditor(line: nil, title: "Add a side") { line in
                    looseLines.append(line)
                }
            }
        }
        .task {
            await recipeStore.refresh()
            guard !loaded else { return }  // don't stomp edits if the task re-runs
            loaded = true
            if let meal = mode.meal {
                name = meal.name
                slot = meal.slot ?? "other"
                selectedRecipes = Set(meal.recipes.map(\.id))
                scales = Dictionary(uniqueKeysWithValues: meal.recipes.map { ($0.id, $0.scale ?? 1) })
                looseLines = meal.looseIngredients.map {
                    LooseLine(name: $0.name, quantity: $0.quantity, unit: $0.unit)
                }
            }
            baseline = snapshot
        }
    }

    private func scaleBinding(_ recipeId: UUID) -> Binding<Double> {
        Binding(get: { scales[recipeId] ?? 1 }, set: { scales[recipeId] = $0 })
    }

    /// Portions in, multiples out. The division is done here rather than sent
    /// as the API's `servings` deliberately: this app can be newer than the
    /// server it talks to, and one that predates servings-scaling would ignore
    /// the key and save ×1 — quietly buying the wrong amount of food is worse
    /// than doing the arithmetic locally. The server-side field is there for
    /// callers that ship with it (the web app, an AI over MCP).
    private func servingsBinding(_ recipeId: UUID, _ recipeServings: Int) -> Binding<Int> {
        Binding(
            get: { max(1, Int((Double(recipeServings) * (scales[recipeId] ?? 1)).rounded())) },
            set: { scales[recipeId] = Double($0) / Double(recipeServings) }
        )
    }

    private func scaleLabel(_ recipe: RecipeSummary) -> String {
        let scale = scales[recipe.id] ?? 1
        let multiple = "×\(IngredientLineEditor.amountText(scale))"
        guard let servings = recipe.servings else { return "\(multiple) — batch cooking" }
        let feeds = "Serves \(IngredientLineEditor.amountText((Double(servings) * scale).rounded()))"
        // The multiple is what the shopping list works in, so keep it visible
        // once it stops being ×1.
        return scale == 1 ? "\(feeds) — the recipe's own" : "\(feeds) — \(multiple)"
    }

    private func addLooseLine() {
        let text = looseEntry.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return }
        let (name, quantity, unit) = ShoppingListView.parseQuickAdd(text)
        looseLines.append(LooseLine(name: name, quantity: quantity, unit: unit))
        looseEntry = ""
        sideFieldFocused = true  // keep typing the next side without re-tapping
    }

    /// The name a meal is saved with. The typed name wins; when it's blank the
    /// selected recipes' titles fill in (joined in library order, clamped to
    /// the API's 300-char name limit), so the strict API contract —
    /// `MealCreate.name` min_length=1 — is met without a backend change.
    /// Empty, i.e. saving blocked, only with no name and no recipes.
    static func resolvedName(
        typed: String, selectedRecipes: Set<UUID>, library: [RecipeSummary]
    ) -> String {
        let trimmed = typed.trimmingCharacters(in: .whitespaces)
        if !trimmed.isEmpty { return trimmed }
        let joined = library
            .filter { selectedRecipes.contains($0.id) }
            .map(\.title)
            .joined(separator: " + ")
        return String(joined.prefix(300))
    }

    private func save() {
        isSaving = true
        errorMessage = nil
        Task {
            defer { isSaving = false }
            let saved: Meal?
            if let existing = mode.meal {
                saved = await planStore.updateMeal(
                    existing,
                    name: resolvedName,
                    slot: slot,
                    recipeIds: Array(selectedRecipes),
                    scales: scales,
                    looseIngredients: looseLines
                )
                if saved != nil {
                    // The server re-synced the list; pull the new one in.
                    await listStore.sync()
                }
            } else {
                saved = await planStore.createMeal(
                    name: resolvedName,
                    slot: slot,
                    recipeIds: Array(selectedRecipes),
                    scales: scales,
                    looseIngredients: looseLines
                )
            }
            if let saved {
                onSaved(saved)
            } else {
                errorMessage = planStore.errorMessage
                planStore.errorMessage = nil
            }
        }
    }
}
