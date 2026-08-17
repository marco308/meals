import SwiftUI

/// The current plan: a pool of meal options grouped by slot — deliberately
/// never a calendar (guiding principle 1).
struct PlanView: View {
    @Environment(PlanStore.self) private var store
    @State private var showAddMeal = false
    @State private var showNewPlan = false
    @State private var showHistory = false
    @State private var showArchiveConfirm = false
    @State private var showRename = false
    @State private var renameDraft = ""

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
            // Stale data is useful; stale data mistaken for current isn't (#33).
            .safeAreaInset(edge: .top) {
                if store.isOffline {
                    OfflineBanner(what: "plan")
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
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("New plan…", systemImage: "calendar.badge.plus") { showNewPlan = true }
                        Button("Past plans", systemImage: "clock.arrow.circlepath") { showHistory = true }
                        if let plan = store.plan {
                            Button("Rename plan…", systemImage: "pencil") {
                                renameDraft = plan.label
                                showRename = true
                            }
                            Button("Archive this plan", systemImage: "archivebox", role: .destructive) {
                                showArchiveConfirm = true
                            }
                        }
                        // Account, household and app info live in the Settings
                        // tab — buried in a plan menu, account deletion is
                        // neither findable by a user nor by App Review.
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
            .sheet(isPresented: $showAddMeal) { AddMealSheet() }
            .sheet(isPresented: $showNewPlan) { NewPlanSheet() }
            .sheet(isPresented: $showHistory) { PastPlansSheet() }
            .alert("Rename plan", isPresented: $showRename) {
                TextField("Label", text: $renameDraft)
                Button("Cancel", role: .cancel) {}
                Button("Rename") {
                    let cleaned = renameDraft.trimmingCharacters(in: .whitespaces)
                    guard !cleaned.isEmpty else { return }
                    Task { await store.renamePlan(to: cleaned) }
                }
            } message: {
                Text("The label is how this week shows up in your past plans.")
            }
            .confirmationDialog(
                "Archive '\(store.plan?.label ?? "")'? Its meals come off the shopping list; anything added by hand stays.",
                isPresented: $showArchiveConfirm,
                titleVisibility: .visible
            ) {
                Button("Archive plan", role: .destructive) {
                    Task { await store.archiveCurrentPlan() }
                }
            }
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
    }
}

/// Start a weekly-ish plan (Q4) — optionally copying a previous week's
/// options ("same as two weeks ago").
struct NewPlanSheet: View {
    @Environment(PlanStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var label = ""
    @State private var copyFrom: PlanSummary?
    @State private var isSaving = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Label (e.g. w/c 27 July)", text: $label)
                }
                Section {
                    Picker("Start from", selection: $copyFrom) {
                        Text("Empty plan").tag(PlanSummary?.none)
                        ForEach(store.history) { plan in
                            Text("\(plan.label) (\(plan.mealCount) meals)").tag(Optional(plan))
                        }
                    }
                } footer: {
                    Text("Copying brings the meals in; their ingredients land on the shopping list straight away.")
                }
                Section {
                    Button(action: save) {
                        if isSaving {
                            ProgressView().frame(maxWidth: .infinity)
                        } else {
                            Text("Create plan").frame(maxWidth: .infinity).fontWeight(.semibold)
                        }
                    }
                    .disabled(isSaving)
                }
            }
            .navigationTitle("New plan")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .task { await store.loadHistory() }
        }
    }

    private func save() {
        isSaving = true
        Task {
            defer { isSaving = false }
            let cleaned = label.trimmingCharacters(in: .whitespaces)
            await store.createPlan(
                label: cleaned.isEmpty ? "This week's options" : cleaned,
                copyFrom: copyFrom?.id
            )
            dismiss()
        }
    }
}

/// Q4's history: past (and other active) plans, and one-tap "again".
struct PastPlansSheet: View {
    @Environment(PlanStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                ForEach(store.history) { plan in
                    NavigationLink {
                        PastPlanDetailView(summary: plan)
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(plan.label)
                                Text("\(plan.status == "archived" ? "archived · " : "active · ")\(plan.mealCount) meal\(plan.mealCount == 1 ? "" : "s")")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            if plan.status == "archived" && plan.mealCount > 0 {
                                Button("Again") {
                                    Task {
                                        await store.createPlan(label: "\(plan.label) (again)", copyFrom: plan.id)
                                        dismiss()
                                    }
                                }
                                .buttonStyle(.bordered)
                                .font(.callout)
                            }
                        }
                    }
                }
                if store.history.isEmpty {
                    ContentUnavailableView(
                        "No plans yet", systemImage: "clock.arrow.circlepath",
                        description: Text("Archived weeks show up here — start one again anytime.")
                    )
                }
            }
            .navigationTitle("Past plans")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .task { await store.loadHistory() }
        }
    }
}

/// What a past week's menu was, read back in full (Q4): every option and
/// whether it got cooked. Read-only on purpose — to cook it again, use
/// "Again" on the plan's row, which copies it into a fresh plan.
struct PastPlanDetailView: View {
    @Environment(Session.self) private var session
    let summary: PlanSummary

    @State private var plan: Plan?
    @State private var errorMessage: String?

    var body: some View {
        Group {
            if let plan {
                List {
                    if let wrapped = TimestampLabel.day(plan.archivedAt) {
                        Section {
                            Label("Wrapped up \(wrapped)", systemImage: "archivebox")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                    }
                    ForEach(plan.slots, id: \.slot) { group in
                        Section(group.slot.capitalized) {
                            ForEach(group.meals) { planMeal in
                                HStack {
                                    Text(planMeal.meal.name)
                                    Spacer()
                                    if planMeal.cookedAt != nil {
                                        Label("cooked", systemImage: "checkmark.circle.fill")
                                            .labelStyle(.iconOnly)
                                            .foregroundStyle(.green)
                                            .accessibilityLabel("cooked")
                                    }
                                }
                            }
                        }
                    }
                    if plan.meals.isEmpty {
                        ContentUnavailableView(
                            "Nothing was on it",
                            systemImage: "fork.knife",
                            description: Text("This plan was wrapped up without any meals.")
                        )
                    }
                }
            } else if let errorMessage {
                ContentUnavailableView(
                    "Couldn't load plan",
                    systemImage: "exclamationmark.triangle",
                    description: Text(errorMessage)
                )
            } else {
                ProgressView()
            }
        }
        .navigationTitle(summary.label)
        .navigationBarTitleDisplayMode(.inline)
        .task {
            do {
                plan = try await session.api.plan(id: summary.id)
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}

struct PlanMealRow: View {
    @Environment(PlanStore.self) private var store
    let planMeal: PlanMeal
    @State private var showEditor = false

    var body: some View {
        NavigationLink {
            destination
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
            Button {
                showEditor = true
            } label: {
                Label("Edit", systemImage: "pencil")
            }
            .tint(.orange)
        }
        // Full swipe is on now that a stray drag is undoable (#51) — the same
        // gesture takes it back, so the worst case is a swipe, not a permanent
        // dent in "what do we actually eat".
        .swipeActions(edge: .leading, allowsFullSwipe: true) {
            if planMeal.cookedAt == nil {
                Button {
                    Task { await store.markCooked(planMeal) }
                } label: {
                    Label("Cooked", systemImage: "checkmark")
                }
                .tint(.green)
            } else {
                Button {
                    Task { await store.undoCooked(planMeal) }
                } label: {
                    Label("Not cooked", systemImage: "arrow.uturn.backward")
                }
                .tint(.orange)
            }
        }
        .sheet(isPresented: $showEditor) {
            NavigationStack {
                MealEditorView(
                    mode: .edit(planMeal.meal),
                    onSaved: { _ in showEditor = false },
                    onCancel: { showEditor = false }
                )
            }
        }
    }

    /// A meal that is exactly one recipe goes straight to the full recipe
    /// (with plan actions on board) — an intermediate meal screen would just
    /// repeat the ingredient list. Composite meals get the meal overview.
    @ViewBuilder
    private var destination: some View {
        let meal = planMeal.meal
        if meal.recipes.count == 1 && meal.looseIngredients.isEmpty {
            RecipeDetailView(recipeId: meal.recipes[0].id, planContext: planMeal)
        } else {
            MealDetailView(planMeal: planMeal)
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
/// Also the library's own housekeeping: a swipe edits or deletes a meal that
/// isn't on the plan — the only place such a meal is ever on screen.
struct AddMealSheet: View {
    @Environment(PlanStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var editingMeal: Meal?
    @State private var pendingDelete: Meal?
    @State private var deleteError: String?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    NavigationLink {
                        MealEditorView { meal in
                            Task {
                                await store.addMeal(meal)
                                dismiss()
                            }
                        }
                    } label: {
                        Label("Create a new meal", systemImage: "plus.circle.fill")
                            .fontWeight(.medium)
                    }
                }

                Section("From the library") {
                    if availableMeals.isEmpty {
                        Text("Every meal in the library is already on the plan.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
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
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            Button(role: .destructive) {
                                pendingDelete = meal
                            } label: {
                                Label("Delete", systemImage: "trash")
                            }
                            Button {
                                editingMeal = meal
                            } label: {
                                Label("Edit", systemImage: "pencil")
                            }
                            .tint(.orange)
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
            .sheet(item: $editingMeal) { meal in
                NavigationStack {
                    MealEditorView(
                        mode: .edit(meal),
                        onSaved: { _ in editingMeal = nil },
                        onCancel: { editingMeal = nil }
                    )
                }
            }
            .confirmationDialog(
                "Delete '\(pendingDelete?.name ?? "")'? Recipes stay in the library; cooked history stays on the record. This can't be undone.",
                isPresented: .init(get: { pendingDelete != nil }, set: { if !$0 { pendingDelete = nil } }),
                titleVisibility: .visible
            ) {
                Button("Delete meal", role: .destructive) {
                    guard let meal = pendingDelete else { return }
                    pendingDelete = nil
                    Task {
                        if await !store.deleteMeal(meal) {
                            deleteError = store.errorMessage
                            store.errorMessage = nil
                        }
                    }
                }
            }
            .alert(
                "Couldn't delete",
                isPresented: .init(get: { deleteError != nil }, set: { if !$0 { deleteError = nil } })
            ) {
                Button("OK") { deleteError = nil }
            } message: {
                Text(deleteError ?? "")
            }
        }
    }

    private var availableMeals: [Meal] {
        let planned = Set(store.plan?.meals.map(\.meal.id) ?? [])
        return store.mealLibrary.filter { !planned.contains($0.id) }
    }
}
