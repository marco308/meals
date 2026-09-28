import SwiftUI

/// The meal library: every meal the household has made, whether or not it is
/// on this week's plan. A meal is what goes on a plan (one or more recipes plus
/// any sides), so it's a different thing from a recipe and gets its own tab,
/// the way the web app gives it its own page. Before this, a meal was only on
/// screen while it was on the plan or while picking one to add.
///
/// Reads come from `PlanStore.mealLibrary`, which is cached on disk, so the
/// library stays browsable offline. Search and the slot filter run on that
/// copy rather than asking the server again.
struct MealsView: View {
    @Environment(PlanStore.self) private var store
    @State private var search = ""
    @State private var slot: String?
    @State private var showNewMeal = false
    @State private var pendingDelete: Meal?
    @State private var actionError: String?
    @State private var loaded = false

    var body: some View {
        NavigationStack {
            List {
                ForEach(visibleMeals) { meal in
                    NavigationLink(value: meal.id) {
                        MealRow(meal: meal, onPlan: plannedIds.contains(meal.id))
                    }
                    .swipeActions(edge: .leading, allowsFullSwipe: true) {
                        if !plannedIds.contains(meal.id) {
                            Button {
                                Task { await addToPlan(meal) }
                            } label: {
                                Label("Add to plan", systemImage: "plus")
                            }
                            .tint(.green)
                        }
                    }
                    // No full swipe: deleting a meal can't be undone, so it
                    // always goes through the confirmation.
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                        Button(role: .destructive) {
                            pendingDelete = meal
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
                }
                if visibleMeals.isEmpty && loaded {
                    // "Empty" and "couldn't reach the server" must never look
                    // the same (#33).
                    if store.isOffline && store.mealLibrary.isEmpty {
                        ContentUnavailableView(
                            "Offline",
                            systemImage: "wifi.slash",
                            description: Text("No saved copy of the meals yet. They'll be here once you've loaded them online.")
                        )
                    } else if !store.mealLibrary.isEmpty {
                        ContentUnavailableView(
                            "Nothing matches",
                            systemImage: "line.3.horizontal.decrease.circle",
                            description: Text("No meal matches the search and filter.")
                        )
                    } else {
                        ContentUnavailableView {
                            Label("No meals yet", systemImage: "fork.knife")
                        } description: {
                            Text("A meal is a recipe (or a few) with any sides. Make one and it becomes a plan option.")
                        } actions: {
                            Button("New meal") { showNewMeal = true }
                        }
                    }
                }
            }
            .safeAreaInset(edge: .top) {
                if store.isOffline && !store.mealLibrary.isEmpty {
                    OfflineBanner(what: "meals")
                }
            }
            .navigationTitle("Meals")
            .navigationDestination(for: UUID.self) { id in
                if let meal = store.mealLibrary.first(where: { $0.id == id }) {
                    MealDetailView(meal: meal)
                } else {
                    ContentUnavailableView("Meal deleted", systemImage: "fork.knife")
                }
            }
            .searchable(text: $search, prompt: "Search meals")
            .toolbar {
                if !availableSlots.isEmpty {
                    ToolbarItem(placement: .topBarTrailing) {
                        Menu {
                            Picker("Slot", selection: $slot) {
                                Text("Any time").tag(String?.none)
                                ForEach(availableSlots, id: \.self) { slot in
                                    Text(slot.capitalizedFirst).tag(Optional(slot))
                                }
                            }
                        } label: {
                            Image(systemName: slot == nil
                                ? "line.3.horizontal.decrease.circle"
                                : "line.3.horizontal.decrease.circle.fill")
                        }
                        .accessibilityLabel("Filter")
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showNewMeal = true
                    } label: {
                        Image(systemName: "plus")
                    }
                    .accessibilityLabel("New meal")
                }
            }
            .sheet(isPresented: $showNewMeal) {
                NavigationStack {
                    MealEditorView(
                        onSaved: { _ in showNewMeal = false },
                        onCancel: { showNewMeal = false }
                    )
                }
            }
            .confirmationDialog(
                deleteQuestion,
                isPresented: .init(get: { pendingDelete != nil }, set: { if !$0 { pendingDelete = nil } }),
                titleVisibility: .visible
            ) {
                Button("Delete meal", role: .destructive) {
                    guard let meal = pendingDelete else { return }
                    pendingDelete = nil
                    Task {
                        if await !store.deleteMeal(meal) {
                            actionError = store.errorMessage
                            store.errorMessage = nil
                        }
                    }
                }
            }
            .alert(
                "Couldn't do that",
                isPresented: .init(get: { actionError != nil }, set: { if !$0 { actionError = nil } })
            ) {
                Button("OK") { actionError = nil }
            } message: {
                Text(actionError ?? "")
            }
            .task { await reload() }
            .refreshable { await reload() }
        }
    }

    private func reload() async {
        // The plan too, so "on the plan" is current when this tab is opened
        // after a change made elsewhere.
        async let library: Void = store.loadMealLibrary()
        async let plan: Void = store.refresh()
        _ = await (library, plan)
        loaded = true
        // A failed read is said here, not left for the Plan tab's alert.
        if let problem = store.errorMessage {
            actionError = problem
            store.errorMessage = nil
        }
    }

    private func addToPlan(_ meal: Meal) async {
        if await !store.addMeal(meal) {
            actionError = store.errorMessage ?? "Couldn't reach the server."
            store.errorMessage = nil
        }
    }

    private var plannedIds: Set<UUID> {
        Set(store.plan?.meals.map(\.meal.id) ?? [])
    }

    /// Slot order first (the way the plan groups them), then name.
    private var visibleMeals: [Meal] {
        let term = search.trimmingCharacters(in: .whitespaces)
        return store.mealLibrary
            .filter { meal in
                guard slot.map({ meal.allSlots.contains($0) }) ?? true else { return false }
                guard !term.isEmpty else { return true }
                return meal.name.localizedCaseInsensitiveContains(term)
                    || meal.recipes.contains { $0.title.localizedCaseInsensitiveContains(term) }
            }
            .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    /// Slots present in the library, in the order they happen in a day, plus
    /// the active one so it can always be cleared.
    private var availableSlots: [String] {
        var slots = Set(store.mealLibrary.flatMap(\.allSlots))
        if let slot { slots.insert(slot) }
        return slots.sorted { MealSlots.precedes([$0], [$1]) }
    }

    private var deleteQuestion: String {
        guard let meal = pendingDelete else { return "" }
        return plannedIds.contains(meal.id)
            ? "Delete '\(meal.name)'? It comes off the plan and its ingredients come off the shopping list. This can't be undone."
            : "Delete '\(meal.name)'? Recipes stay in the library; cooked history stays on the record. This can't be undone."
    }
}

struct MealRow: View {
    let meal: Meal
    let onPlan: Bool

    var body: some View {
        HStack(spacing: 12) {
            RecipeThumbnail(imageUrl: meal.recipes.lazy.compactMap(\.imageUrl).first)
            VStack(alignment: .leading, spacing: 3) {
                Text(meal.name)
                if let contents {
                    Text(contents)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                HStack(spacing: 8) {
                    if let slots = meal.slotsLabel {
                        Text(slots)
                    }
                    if let cooked = meal.cookedSummary {
                        Text(cooked)
                    }
                    if onPlan {
                        Text("\(Image(systemName: "checkmark.circle.fill")) on the plan")
                            .foregroundStyle(.green)
                    }
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            }
        }
    }

    /// "Spaghetti Bolognese + extras", like the web app's meal cards.
    private var contents: String? {
        let recipes = meal.recipes.map(\.title).joined(separator: " + ")
        switch (recipes.isEmpty, meal.looseIngredients.isEmpty) {
        case (true, true): return nil
        case (true, false): return "Sides only"
        case (false, true): return recipes
        case (false, false): return recipes + " + extras"
        }
    }
}
