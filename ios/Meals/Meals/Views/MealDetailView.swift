import SwiftUI

/// F2's "tap a meal" view: every ingredient the meal puts on the list —
/// grouped by recipe, plus the loose ones — with links through to full
/// recipe details and out to the original pages. Also where the meal gets
/// edited or deleted (issue #16).
struct MealDetailView: View {
    @Environment(PlanStore.self) private var planStore
    @Environment(RecipeStore.self) private var recipeStore
    @Environment(\.dismiss) private var dismiss

    let planMeal: PlanMeal
    @State private var recipeDetails: [UUID: Recipe] = [:]
    @State private var reloadKey = 0
    @State private var showEditor = false

    /// "×2" beside a scaled recipe — otherwise the meal reads as a single
    /// batch while the shopping list says otherwise (#32).
    static func scaleBadge(_ scale: Double?) -> String? {
        guard let scale, scale != 1 else { return nil }
        return "×\(IngredientLineEditor.amountText(scale))"
    }
    @State private var showDeleteConfirm = false

    /// The plan is refetched after an edit, so read the meal back from the
    /// store where possible — the passed-in copy is a snapshot.
    private var meal: Meal {
        planStore.plan?.meals.first { $0.id == planMeal.id }?.meal ?? planMeal.meal
    }

    var body: some View {
        List {
            if planMeal.cookedAt != nil {
                Section {
                    Label("Cooked", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                }
            }

            if let cooked = meal.cookedSummary {
                Section {
                    Label(cooked, systemImage: "flame")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }

            ForEach(meal.recipes) { recipe in
                Section {
                    NavigationLink {
                        RecipeDetailView(recipeId: recipe.id)
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 6) {
                                Text(recipe.title).fontWeight(.medium)
                                if let badge = Self.scaleBadge(recipe.scale) {
                                    Text(badge)
                                        .font(.caption)
                                        .fontWeight(.semibold)
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 2)
                                        .background(.tint.opacity(0.15), in: Capsule())
                                        .accessibilityLabel("scaled \(badge)")
                                }
                            }
                            HStack(spacing: 8) {
                                // The scaled figure when there is one: on a meal
                                // screen "serves 6" means this meal, not the
                                // recipe's own printed yield (#53).
                                if let servings = recipe.scaledServings ?? recipe.servings {
                                    Label("\(servings)", systemImage: "person.2")
                                }
                                if let minutes = recipe.totalMinutes {
                                    Label("\(minutes) min", systemImage: "clock")
                                }
                            }
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        }
                    }
                    if let lines = recipeDetails[recipe.id]?.ingredients {
                        ForEach(lines) { line in
                            IngredientLineRow(line: line) { reloadKey += 1 }
                        }
                    } else {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                } header: {
                    Text("Recipe")
                }
            }

            if !meal.looseIngredients.isEmpty {
                Section("On the side") {
                    ForEach(meal.looseIngredients) { line in
                        IngredientLineRow(line: line) { reloadKey += 1 }
                    }
                }
            }

            Section {
                if planMeal.cookedAt == nil {
                    Button {
                        Task {
                            await planStore.markCooked(planMeal)
                            dismiss()
                        }
                    } label: {
                        Label("Mark as cooked", systemImage: "checkmark")
                    }
                } else {
                    // Undo lives where the mistake is noticed (#51): this is the
                    // screen showing the "Cooked" badge someone didn't expect.
                    Button {
                        Task {
                            await planStore.undoCooked(planMeal)
                            dismiss()
                        }
                    } label: {
                        Label("Not cooked after all", systemImage: "arrow.uturn.backward")
                    }
                }
                Button(role: .destructive) {
                    Task {
                        await planStore.removeMeal(planMeal)
                        dismiss()
                    }
                } label: {
                    Label("Remove from plan", systemImage: "trash")
                }
            } footer: {
                Text("Removing a meal takes its ingredients off the shopping list; anything you added by hand stays.")
            }
        }
        .navigationTitle(meal.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button("Edit meal", systemImage: "pencil") { showEditor = true }
                    Button("Delete meal", systemImage: "trash", role: .destructive) {
                        showDeleteConfirm = true
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
        }
        .sheet(isPresented: $showEditor) {
            NavigationStack {
                MealEditorView(
                    mode: .edit(meal),
                    onSaved: { _ in
                        showEditor = false
                        reloadKey += 1  // recipe ingredient lists may have changed
                    },
                    onCancel: { showEditor = false }
                )
            }
        }
        .confirmationDialog(
            "Delete '\(meal.name)'? It comes off the plan and its ingredients come off the shopping list. This can't be undone.",
            isPresented: $showDeleteConfirm,
            titleVisibility: .visible
        ) {
            Button("Delete meal", role: .destructive) {
                Task {
                    if await planStore.deleteMeal(meal) { dismiss() }
                }
            }
        }
        .task(id: reloadKey) {
            for recipe in meal.recipes where reloadKey > 0 || recipeDetails[recipe.id] == nil {
                recipeDetails[recipe.id] = try? await recipeStore.detail(id: recipe.id)
            }
        }
    }
}
