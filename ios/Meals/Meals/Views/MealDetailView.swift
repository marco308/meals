import SwiftUI

/// F2's "tap a meal" view: every ingredient the meal puts on the list —
/// grouped by recipe, plus the loose ones — with links through to full
/// recipe details and out to the original pages.
struct MealDetailView: View {
    @Environment(PlanStore.self) private var planStore
    @Environment(RecipeStore.self) private var recipeStore
    @Environment(\.dismiss) private var dismiss

    let planMeal: PlanMeal
    @State private var recipeDetails: [UUID: Recipe] = [:]
    @State private var reloadKey = 0

    private var meal: Meal { planMeal.meal }

    var body: some View {
        List {
            if planMeal.cookedAt != nil {
                Section {
                    Label("Cooked", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                }
            }

            ForEach(meal.recipes) { recipe in
                Section {
                    NavigationLink {
                        RecipeDetailView(recipeId: recipe.id)
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(recipe.title).fontWeight(.medium)
                            HStack(spacing: 8) {
                                if let servings = recipe.servings {
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
                            ingredientRow(line)
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
                        ingredientRow(line)
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
        .task(id: reloadKey) {
            for recipe in meal.recipes where reloadKey > 0 || recipeDetails[recipe.id] == nil {
                recipeDetails[recipe.id] = try? await recipeStore.detail(id: recipe.id)
            }
        }
    }

    private func ingredientRow(_ line: RecipeLine) -> some View {
        NavigationLink {
            IngredientEditorView(ingredientId: line.ingredientId) { reloadKey += 1 }
        } label: {
            HStack {
                Text(line.aisle)
                Text(line.name)
                Spacer()
                Text(line.display)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            .font(.callout)
        }
    }
}
