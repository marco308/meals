import SwiftUI

struct RecipesView: View {
    @Environment(RecipeStore.self) private var store
    @Environment(PlanStore.self) private var planStore
    @State private var search = ""
    @State private var showIngest = false
    @State private var pendingDelete: RecipeSummary?
    @State private var deleteError: String?

    var body: some View {
        NavigationStack {
            List {
                ForEach(store.recipes) { recipe in
                    NavigationLink(value: recipe.id) {
                        RecipeRow(recipe: recipe)
                    }
                    // No full swipe: deleting a recipe can't be undone, so it
                    // always goes through the confirmation.
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                        Button(role: .destructive) {
                            pendingDelete = recipe
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
                }
                if store.recipes.isEmpty && !store.isLoading {
                    // "Empty" and "couldn't reach the server" are different
                    // things and must never look the same (#33).
                    if store.isOffline {
                        ContentUnavailableView(
                            "Offline",
                            systemImage: "wifi.slash",
                            description: Text("No saved copy of the library yet — it'll be here once you've loaded it online.")
                        )
                    } else if hasFilters || !search.isEmpty {
                        ContentUnavailableView(
                            "Nothing matches",
                            systemImage: "line.3.horizontal.decrease.circle",
                            description: Text("No recipe matches the search and filters.")
                        )
                    } else {
                        ContentUnavailableView(
                            "No recipes yet",
                            systemImage: "book",
                            description: Text("Add one from a URL — most recipe sites parse automatically.")
                        )
                    }
                }
            }
            .safeAreaInset(edge: .top) {
                if store.isOffline && !store.recipes.isEmpty {
                    OfflineBanner(what: "library")
                }
            }
            .navigationTitle("Recipes")
            .navigationDestination(for: UUID.self) { id in
                RecipeDetailView(recipeId: id)
            }
            .searchable(text: $search, prompt: "Search the library")
            .onChange(of: search) { _, term in
                Task { await store.refresh(search: term.isEmpty ? nil : term) }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Picker("Sort", selection: sortBinding) {
                            ForEach(RecipeSort.allCases, id: \.self) { option in
                                Text(option.label).tag(option)
                            }
                        }
                        Divider()
                        Toggle("Under 30 min", isOn: under30Binding)
                        if !availableTags.isEmpty {
                            Picker("Tag", selection: tagBinding) {
                                Text("Any tag").tag(String?.none)
                                ForEach(availableTags, id: \.self) { tag in
                                    Text(tag).tag(Optional(tag))
                                }
                            }
                            .pickerStyle(.menu)
                        }
                    } label: {
                        Image(systemName: hasFilters
                            ? "line.3.horizontal.decrease.circle.fill"
                            : "line.3.horizontal.decrease.circle")
                    }
                    .accessibilityLabel("Sort and filter")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showIngest = true
                    } label: {
                        Image(systemName: "link.badge.plus")
                    }
                }
            }
            .sheet(isPresented: $showIngest) { IngestSheet() }
            .confirmationDialog(
                "Delete '\(pendingDelete?.title ?? "")'? This can't be undone.",
                isPresented: .init(get: { pendingDelete != nil }, set: { if !$0 { pendingDelete = nil } }),
                titleVisibility: .visible
            ) {
                Button("Delete recipe", role: .destructive) {
                    guard let recipe = pendingDelete else { return }
                    pendingDelete = nil
                    Task { deleteError = await store.delete(id: recipe.id) }
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
            .task { await store.refresh() }
            .refreshable { await store.refresh(search: search.isEmpty ? nil : search) }
        }
    }

    private var sortBinding: Binding<RecipeSort> {
        Binding(
            get: { store.sort },
            set: { option in
                store.sort = option
                Task { await store.refresh(search: search.isEmpty ? nil : search) }
            }
        )
    }

    private var hasFilters: Bool { store.under30 || store.tag != nil }

    /// Tags present in the current results, plus the active one so it can
    /// always be untoggled — same behaviour as the web app's tag chips.
    private var availableTags: [String] {
        var tags = Set(store.recipes.flatMap(\.tags))
        if let active = store.tag { tags.insert(active) }
        return tags.sorted()
    }

    private var under30Binding: Binding<Bool> {
        Binding(
            get: { store.under30 },
            set: { on in
                store.under30 = on
                Task { await store.refresh(search: search.isEmpty ? nil : search) }
            }
        )
    }

    private var tagBinding: Binding<String?> {
        Binding(
            get: { store.tag },
            set: { tag in
                store.tag = tag
                Task { await store.refresh(search: search.isEmpty ? nil : search) }
            }
        )
    }
}

struct RecipeRow: View {
    let recipe: RecipeSummary

    var body: some View {
        HStack(spacing: 12) {
            RecipeThumbnail(imageUrl: recipe.imageUrl)
            details
        }
    }

    private var details: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(recipe.title)
            HStack(spacing: 8) {
                // Servings and time are the facts you scan the library for,
                // so they keep their width and the tags truncate instead.
                if let servings = recipe.servings {
                    Label("\(servings)", systemImage: "person.2").layoutPriority(1)
                }
                if let minutes = recipe.totalMinutes {
                    Label("\(minutes) min", systemImage: "clock").layoutPriority(1)
                }
                ForEach(recipe.tags.prefix(2), id: \.self) { tag in
                    Text(tag)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1)
                        .background(.quaternary, in: Capsule())
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            // The thumbnail costs the row real width: without this the times
            // wrap mid-label ("80 / min") instead of the tags giving way.
            .lineLimit(1)
            if let cooked = recipe.cookedSummary {
                Text(cooked)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct RecipeDetailView: View {
    @Environment(RecipeStore.self) private var store
    @Environment(PlanStore.self) private var planStore
    @Environment(\.dismiss) private var dismiss
    let recipeId: UUID
    /// Set when reached from the plan: the recipe IS the meal (single-recipe
    /// meals skip the redundant meal screen), so plan actions live here.
    var planContext: PlanMeal? = nil
    @State private var recipe: Recipe?
    @State private var errorMessage: String?
    @State private var addedMealName: String?
    @State private var addError: String?
    @State private var reloadKey = 0
    @State private var showDeleteConfirm = false
    @State private var deleteError: String?
    @State private var showEditor = false

    /// "×1.5 — serves 6" when the meal this screen stands in for scales the
    /// recipe, nil when it doesn't or when the recipe was opened from the
    /// library. `scaledServings` is absent on a backend that predates it, so
    /// the multiple alone is the fallback.
    private var mealScaling: String? {
        guard let link = planContext?.meal.recipes.first(where: { $0.id == recipeId }),
              let scale = link.scale, scale != 1
        else { return nil }
        let multiple = "×\(IngredientLineEditor.amountText(scale))"
        guard let feeds = link.scaledServings else { return multiple }
        return "\(multiple) — serves \(feeds)"
    }

    var body: some View {
        Group {
            if let recipe {
                detail(recipe)
            } else if let errorMessage {
                ContentUnavailableView("Couldn't load recipe", systemImage: "exclamationmark.triangle", description: Text(errorMessage))
            } else {
                ProgressView()
            }
        }
        .task(id: reloadKey) {
            do {
                recipe = try await store.detail(id: recipeId)
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    private func detail(_ recipe: Recipe) -> some View {
        List {
            if RecipeImageURL.parse(recipe.imageUrl) != nil {
                Section {
                    RecipeHeroImage(imageUrl: recipe.imageUrl)
                        .listRowInsets(EdgeInsets())
                }
            }

            if planContext?.cookedAt != nil {
                Section {
                    Label("Cooked", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                }
            }

            Section {
                if let servings = recipe.servings {
                    LabeledContent("Serves", value: "\(servings)")
                }
                // A single-recipe meal opens this screen instead of the meal
                // screen, so scaling set on the meal would otherwise be
                // invisible exactly where it matters (#53). The amounts below
                // stay the recipe's own — the scaled quantities are on the
                // shopping list, which is what the scale is for.
                if let scaled = mealScaling {
                    LabeledContent("This meal", value: scaled)
                }
                if let prep = recipe.prepMinutes {
                    LabeledContent("Prep", value: "\(prep) min")
                }
                if let cook = recipe.cookMinutes {
                    LabeledContent("Cook", value: "\(cook) min")
                }
                if let times = recipe.timesCooked {
                    LabeledContent("Cooked", value: times == 0 ? "never" : "\(times)×")
                }
                if let last = CookedHistory.monthLabel(recipe.lastCookedAt) {
                    LabeledContent("Last cooked", value: last)
                }
                if let source = recipe.sourceUrl, let url = URL(string: source) {
                    Link(destination: url) {
                        Label("Open original recipe", systemImage: "safari")
                    }
                }
                if recipe.edited {
                    // Says why re-ingesting the URL won't change anything: an
                    // edited recipe is never overwritten by a re-parse (Q3).
                    Label("Edited here — your corrections are kept", systemImage: "pencil.circle")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }

            Section {
                ForEach(recipe.ingredients) { line in
                    IngredientLineRow(line: line) { reloadKey += 1 }
                }
            } header: {
                Text("Ingredients")
            } footer: {
                Text("Tap an ingredient to set its aisle, staple flag, or whether the premium version is worth it. \(Image(systemName: "cabinet")) marks a staple — it stays off the shopping list until the staples check.")
            }

            if let instructions = recipe.instructions, !instructions.isEmpty {
                Section("Method") {
                    Text(instructions)
                        .font(.callout)
                }
            }

            if let planMeal = planContext {
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
            } else {
                Section {
                    Button {
                        Task {
                            // No active plan? One is started, rather than the
                            // tap quietly doing nothing. And if anything fails
                            // the user hears about it, instead of being told
                            // the meal is on a plan that doesn't exist.
                            if let meal = await planStore.addRecipe(recipe) {
                                addedMealName = meal.name
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
                    Text("Starts a plan for you if there isn't one yet.")
                }
            }
        }
        .navigationTitle(recipe.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button("Edit recipe", systemImage: "pencil") { showEditor = true }
                    // Deleting only outside the plan: from a plan meal the
                    // destructive action that makes sense is "remove from
                    // plan", already in the list.
                    if planContext == nil {
                        Button("Delete recipe", systemImage: "trash", role: .destructive) {
                            showDeleteConfirm = true
                        }
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
        }
        .sheet(isPresented: $showEditor) {
            NavigationStack {
                RecipeEditorView(recipe: recipe) { updated in
                    self.recipe = updated
                    showEditor = false
                }
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { showEditor = false }
                    }
                }
            }
        }
        .alert("Added to plan", isPresented: .init(get: { addedMealName != nil }, set: { if !$0 { addedMealName = nil } })) {
            Button("OK") { addedMealName = nil }
        } message: {
            // Naming the plan is what tells you a new one was just started.
            Text("\(addedMealName ?? "") is on '\(planStore.plan?.label ?? "the plan")' and its ingredients are on the shopping list.")
        }
        .alert(
            "Couldn't add to the plan",
            isPresented: .init(get: { addError != nil }, set: { if !$0 { addError = nil } })
        ) {
            Button("OK") { addError = nil }
        } message: {
            Text(addError ?? "")
        }
        .confirmationDialog(
            "Delete '\(recipe.title)'? This can't be undone.",
            isPresented: $showDeleteConfirm,
            titleVisibility: .visible
        ) {
            Button("Delete recipe", role: .destructive) {
                Task {
                    if let problem = await store.delete(id: recipe.id) {
                        deleteError = problem
                    } else {
                        dismiss()
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

struct IngestSheet: View {
    @Environment(RecipeStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var url = ""
    @State private var isWorking = false
    @State private var message: String?
    @State private var succeeded = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Recipe URL") {
                    TextField("https://…", text: $url)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                if let message {
                    Section {
                        Label(message, systemImage: succeeded ? "checkmark.circle" : "info.circle")
                            .foregroundStyle(succeeded ? .green : .orange)
                            .font(.callout)
                    }
                }
                Section {
                    Button(action: ingest) {
                        if isWorking {
                            ProgressView().frame(maxWidth: .infinity)
                        } else {
                            Text("Add recipe").frame(maxWidth: .infinity).fontWeight(.semibold)
                        }
                    }
                    .disabled(isWorking || url.isEmpty)
                } footer: {
                    Text("Most recipe sites parse instantly. Pages without structured data can be added by your AI assistant through the API instead.")
                }
            }
            .navigationTitle("Add from URL")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(succeeded ? "Done" : "Cancel") { dismiss() }
                }
            }
        }
    }

    private func ingest() {
        isWorking = true
        message = nil
        Task {
            defer { isWorking = false }
            do {
                let result = try await store.ingest(url: url)
                succeeded = true
                message = result.cached
                    ? "Already in the library: \(result.recipe.title)"
                    : "Added: \(result.recipe.title)"
            } catch {
                succeeded = false
                message = error.localizedDescription
            }
        }
    }
}
