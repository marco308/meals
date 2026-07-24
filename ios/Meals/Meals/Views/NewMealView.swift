import SwiftUI

/// Inline meal creation (F2: "picking meals from the library, or creating new
/// ones inline"). A meal = a name, a slot, any recipes, and loose sides —
/// "cottage pie with peas and carrots" needs no carrot recipe.
struct NewMealView: View {
    @Environment(PlanStore.self) private var planStore
    @Environment(RecipeStore.self) private var recipeStore

    /// Called with the created meal so the presenting sheet can add-to-plan
    /// and dismiss.
    let onCreated: (Meal) -> Void

    @State private var name = ""
    @State private var slot = "dinner"
    @State private var selectedRecipes: Set<UUID> = []
    @State private var looseLines: [LooseLine] = []
    @State private var looseEntry = ""
    @State private var isSaving = false
    @State private var errorMessage: String?
    @FocusState private var sideFieldFocused: Bool

    private let slots = ["dinner", "lunch", "breakfast", "other"]

    var body: some View {
        Form {
            Section {
                TextField("Meal name (e.g. Cottage pie with peas)", text: $name)
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
                        Text("Create and add to plan")
                            .frame(maxWidth: .infinity)
                            .fontWeight(.semibold)
                    }
                }
                .disabled(isSaving || name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .navigationTitle("New meal")
        .navigationBarTitleDisplayMode(.inline)
        .task { await recipeStore.refresh() }
    }

    private func addLooseLine() {
        let text = looseEntry.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return }
        let (name, quantity, unit) = ShoppingListView.parseQuickAdd(text)
        looseLines.append(LooseLine(name: name, quantity: quantity, unit: unit))
        looseEntry = ""
        sideFieldFocused = true  // keep typing the next side without re-tapping
    }

    private func save() {
        isSaving = true
        errorMessage = nil
        Task {
            defer { isSaving = false }
            if let meal = await planStore.createMeal(
                name: name.trimmingCharacters(in: .whitespaces),
                slot: slot,
                recipeIds: Array(selectedRecipes),
                looseIngredients: looseLines
            ) {
                onCreated(meal)
            } else {
                errorMessage = planStore.errorMessage
                planStore.errorMessage = nil
            }
        }
    }
}
