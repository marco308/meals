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

    @State private var name = ""
    @State private var slot = "dinner"
    @State private var selectedRecipes: Set<UUID> = []
    @State private var looseLines: [LooseLine] = []
    @State private var looseEntry = ""
    @State private var isSaving = false
    @State private var errorMessage: String?
    @State private var loaded = false
    @FocusState private var sideFieldFocused: Bool

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

            Section("Recipes") {
                if recipeStore.recipes.isEmpty {
                    Text("No recipes in the library yet — a meal can be just a name and sides.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                ForEach(recipeStore.recipes) { recipe in
                    Button {
                        if selectedRecipes.contains(recipe.id) {
                            selectedRecipes.remove(recipe.id)
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
                }
            }

            Section {
                ForEach(looseLines) { line in
                    Text(line.display)
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
            } header: {
                Text("On the side")
            } footer: {
                Text("Loose ingredients go straight on the shopping list with the meal — no recipe needed.")
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
        .task {
            await recipeStore.refresh()
            guard !loaded else { return }  // don't stomp edits if the task re-runs
            loaded = true
            if let meal = mode.meal {
                name = meal.name
                slot = meal.slot ?? "other"
                selectedRecipes = Set(meal.recipes.map(\.id))
                looseLines = meal.looseIngredients.map {
                    LooseLine(name: $0.name, quantity: $0.quantity, unit: $0.unit)
                }
            }
        }
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
