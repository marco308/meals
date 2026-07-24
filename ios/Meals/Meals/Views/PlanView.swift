import SwiftUI

/// The current plan: a pool of meal options grouped by slot — deliberately
/// never a calendar (guiding principle 1).
struct PlanView: View {
    @Environment(PlanStore.self) private var store
    @Environment(Session.self) private var session
    @State private var showAddMeal = false
    @State private var newPlanLabel = ""
    @State private var showNewPlan = false

    var body: some View {
        NavigationStack {
            Group {
                if let plan = store.plan {
                    planList(plan)
                } else if store.isLoading {
                    ProgressView()
                } else {
                    noPlanView
                }
            }
            .navigationTitle(store.plan?.label ?? "Plan")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    if store.plan != nil {
                        Button {
                            showAddMeal = true
                        } label: {
                            Image(systemName: "plus")
                        }
                    }
                }
                ToolbarItem(placement: .topBarLeading) {
                    Button("Log out") { session.logOut() }
                        .font(.caption)
                }
            }
            .sheet(isPresented: $showAddMeal) { AddMealSheet() }
            .task { await store.refresh() }
            .refreshable { await store.refresh() }
            .alert("Something went wrong", isPresented: errorBinding) {
                Button("OK") { store.errorMessage = nil }
            } message: {
                Text(store.errorMessage ?? "")
            }
        }
    }

    private var errorBinding: Binding<Bool> {
        Binding(get: { store.errorMessage != nil }, set: { if !$0 { store.errorMessage = nil } })
    }

    private func planList(_ plan: Plan) -> some View {
        List {
            ForEach(plan.slots, id: \.slot) { group in
                Section(group.slot.capitalized) {
                    ForEach(group.meals) { planMeal in
                        PlanMealRow(planMeal: planMeal)
                    }
                }
            }
            if plan.meals.isEmpty {
                ContentUnavailableView(
                    "No meals yet",
                    systemImage: "fork.knife",
                    description: Text("Add options for the week with +")
                )
            }
        }
    }

    private var noPlanView: some View {
        ContentUnavailableView {
            Label("No active plan", systemImage: "list.bullet.rectangle")
        } description: {
            Text("Start a pool of meal options for the week — no days, no schedule.")
        } actions: {
            Button("Start a plan") { showNewPlan = true }
                .buttonStyle(.borderedProminent)
        }
        .alert("New plan", isPresented: $showNewPlan) {
            TextField("Label (e.g. w/c 27 July)", text: $newPlanLabel)
            Button("Create") {
                let label = newPlanLabel.isEmpty ? "This week's options" : newPlanLabel
                Task { await store.createPlan(label: label) }
            }
            Button("Cancel", role: .cancel) {}
        }
    }
}

struct PlanMealRow: View {
    @Environment(PlanStore.self) private var store
    let planMeal: PlanMeal

    var body: some View {
        NavigationLink {
            MealDetailView(planMeal: planMeal)
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(planMeal.meal.name)
                        .font(.body)
                        .strikethrough(planMeal.cookedAt != nil, color: .secondary)
                    if planMeal.cookedAt != nil {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                            .font(.caption)
                    }
                }
                if let subtitle {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .swipeActions(edge: .trailing) {
            Button(role: .destructive) {
                Task { await store.removeMeal(planMeal) }
            } label: {
                Label("Remove", systemImage: "trash")
            }
        }
        // allowsFullSwipe off: a stray horizontal drag while scrolling must
        // not silently mark a meal cooked (there is no un-cook in v1).
        .swipeActions(edge: .leading, allowsFullSwipe: false) {
            Button {
                Task { await store.markCooked(planMeal) }
            } label: {
                Label("Cooked", systemImage: "checkmark")
            }
            .tint(.green)
        }
    }

    private var subtitle: String? {
        var parts: [String] = []
        let recipes = planMeal.meal.recipes
        if !recipes.isEmpty {
            parts.append(recipes.map(\.title).joined(separator: ", "))
        }
        if let minutes = recipes.compactMap(\.totalMinutes).max() {
            parts.append("\(minutes) min")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

/// Pick an existing meal from the library, or create one from a recipe.
struct AddMealSheet: View {
    @Environment(PlanStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if availableMeals.isEmpty {
                    ContentUnavailableView(
                        "Library is empty",
                        systemImage: "book",
                        description: Text("Ingest a recipe in the Recipes tab, then create a meal from it.")
                    )
                }
                ForEach(availableMeals) { meal in
                    Button {
                        Task {
                            await store.addMeal(meal)
                            dismiss()
                        }
                    } label: {
                        VStack(alignment: .leading) {
                            Text(meal.name).foregroundStyle(.primary)
                            if let slot = meal.slot {
                                Text(slot.capitalized).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Add a meal option")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .task { await store.loadMealLibrary() }
        }
    }

    private var availableMeals: [Meal] {
        let planned = Set(store.plan?.meals.map(\.meal.id) ?? [])
        return store.mealLibrary.filter { !planned.contains($0.id) }
    }
}
