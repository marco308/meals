import SwiftUI

/// F2's "tap a meal" view: every ingredient the meal puts on the list —
/// grouped by recipe, plus the loose ones — with links through to full
/// recipe details and out to the original pages. Also where the meal gets
/// edited or deleted (issue #16).
///
/// Reached two ways: from the plan, where it carries the plan actions (cooked,
/// remove), and from the Meals library, where the one action is putting the
/// meal on the plan.
struct MealDetailView: View {
    @Environment(PlanStore.self) private var planStore
    @Environment(RecipeStore.self) private var recipeStore
    @Environment(\.dismiss) private var dismiss

    /// The meal as it was when the screen opened; `meal` reads the live copy.
    private let initialMeal: Meal
    /// Set when reached from the plan.
    private let planMeal: PlanMeal?

    init(planMeal: PlanMeal) {
        self.initialMeal = planMeal.meal
        self.planMeal = planMeal
    }

    init(meal: Meal) {
        self.initialMeal = meal
        self.planMeal = nil
    }

    @State private var recipeDetails: [UUID: Recipe] = [:]
    @State private var reloadKey = 0
    @State private var showEditor = false

    /// "×2" beside a scaled recipe — otherwise the meal reads as a single
    /// batch while the shopping list says otherwise (#32).
    static func scaleBadge(_ scale: Double?) -> String? {
        guard let scale, scale != 1 else { return nil }
        return "×\(MealsUnits.amountText(scale))"
    }
    @State private var showDeleteConfirm = false
    @State private var addedToPlan = false
    @State private var addError: String?

    /// The plan is refetched after an edit, so read the meal back from the
    /// store where possible — the passed-in copy is a snapshot.
    private var meal: Meal {
        if let planMeal {
            return planStore.plan?.meals.first { $0.id == planMeal.id }?.meal ?? planMeal.meal
        }
        return planStore.mealLibrary.first { $0.id == initialMeal.id } ?? initialMeal
    }

    /// Where this meal sits on the current plan, if it does: opened from the
    /// library, it may be on the plan already.
    private var onPlan: PlanMeal? {
        planMeal.flatMap { own in planStore.plan?.meals.first { $0.id == own.id } ?? own }
            ?? planStore.plan?.meals.first { $0.meal.id == initialMeal.id }
    }

    var body: some View {
        List {
            if planMeal?.cookedAt != nil {
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

            if let planMeal {
                planActions(planMeal)
            } else {
                libraryActions
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
            onPlan == nil
                ? "Delete '\(meal.name)'? Recipes stay in the library; cooked history stays on the record. This can't be undone."
                : "Delete '\(meal.name)'? It comes off the plan and its ingredients come off the shopping list. This can't be undone.",
            isPresented: $showDeleteConfirm,
            titleVisibility: .visible
        ) {
            Button("Delete meal", role: .destructive) {
                Task {
                    if await planStore.deleteMeal(meal) { dismiss() }
                }
            }
        }
        .alert("Added to plan", isPresented: $addedToPlan) {
            Button("OK") {}
        } message: {
            // Naming the plan is what tells you a new one was just started.
            Text("\(meal.name) is on '\(planStore.plan?.label ?? "the plan")' and its ingredients are on the shopping list.")
        }
        .alert(
            "Couldn't add to the plan",
            isPresented: .init(get: { addError != nil }, set: { if !$0 { addError = nil } })
        ) {
            Button("OK") { addError = nil }
        } message: {
            Text(addError ?? "")
        }
        .task(id: reloadKey) {
            for recipe in meal.recipes where reloadKey > 0 || recipeDetails[recipe.id] == nil {
                recipeDetails[recipe.id] = try? await recipeStore.detail(id: recipe.id)
            }
        }
    }

    private func planActions(_ planMeal: PlanMeal) -> some View {
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

    @ViewBuilder
    private var libraryActions: some View {
        if let onPlan {
            Section {
                Label(
                    onPlan.cookedAt == nil ? "On this week's plan" : "On the plan, cooked",
                    systemImage: "checkmark.circle"
                )
                .foregroundStyle(.secondary)
            } footer: {
                Text("Mark it cooked or take it off from the Plan tab.")
            }
        } else {
            Section {
                Button {
                    Task {
                        // Same route as a recipe's "Add to this week's plan":
                        // no active plan starts one, and a failure is said.
                        if await planStore.addMeal(meal) {
                            addedToPlan = true
                        } else {
                            addError = planStore.errorMessage ?? "Couldn't reach the server."
                            planStore.errorMessage = nil
                        }
                    }
                } label: {
                    Label("Add to this week's plan", systemImage: "plus.circle.fill")
                        .fontWeight(.medium)
                }
            } footer: {
                Text("Its ingredients join the shopping list. Starts a plan for you if there isn't one yet.")
            }
        }
    }
}
