import SwiftUI

struct RecipesView: View {
    @Environment(RecipeStore.self) private var store
    @Environment(PlanStore.self) private var planStore
    @State private var search = ""
    @State private var showIngest = false

    var body: some View {
        NavigationStack {
            List {
                ForEach(store.recipes) { recipe in
                    NavigationLink(value: recipe.id) {
                        RecipeRow(recipe: recipe)
                    }
                }
                if store.recipes.isEmpty && !store.isLoading {
                    ContentUnavailableView(
                        "No recipes yet",
                        systemImage: "book",
                        description: Text("Add one from a URL — most recipe sites parse automatically.")
                    )
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
                    Button {
                        showIngest = true
                    } label: {
                        Image(systemName: "link.badge.plus")
                    }
                }
            }
            .sheet(isPresented: $showIngest) { IngestSheet() }
            .task { await store.refresh() }
            .refreshable { await store.refresh(search: search.isEmpty ? nil : search) }
        }
    }
}

struct RecipeRow: View {
    let recipe: RecipeSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(recipe.title)
            HStack(spacing: 8) {
                if let servings = recipe.servings {
                    Label("\(servings)", systemImage: "person.2")
                }
                if let minutes = recipe.totalMinutes {
                    Label("\(minutes) min", systemImage: "clock")
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
    @State private var reloadKey = 0

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
                if let prep = recipe.prepMinutes {
                    LabeledContent("Prep", value: "\(prep) min")
                }
                if let cook = recipe.cookMinutes {
                    LabeledContent("Cook", value: "\(cook) min")
                }
                if let source = recipe.sourceUrl, let url = URL(string: source) {
                    Link(destination: url) {
                        Label("Open original recipe", systemImage: "safari")
                    }
                }
            }

            Section {
                ForEach(recipe.ingredients) { line in
                    NavigationLink {
                        IngredientEditorView(ingredientId: line.ingredientId) { reloadKey += 1 }
                    } label: {
                        HStack {
                            Text(line.aisle)
                            Text(line.name)
                            if !line.tier.badge.isEmpty {
                                Text(line.tier.badge).font(.caption2)
                            }
                            Spacer()
                            Text(line.display)
                                .foregroundStyle(.secondary)
                                .monospacedDigit()
                        }
                    }
                }
            } header: {
                Text("Ingredients")
            } footer: {
                Text("Tap an ingredient to set its aisle, staple flag, or whether the premium version is worth it.")
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
                            if let meal = await planStore.createMeal(name: recipe.title, slot: "dinner", recipeIds: [recipe.id]) {
                                await planStore.addMeal(meal)
                                addedMealName = meal.name
                            }
                        }
                    } label: {
                        Label("Add to this week's plan", systemImage: "plus.circle.fill")
                            .fontWeight(.medium)
                    }
                }
            }
        }
        .navigationTitle(recipe.title)
        .navigationBarTitleDisplayMode(.inline)
        .alert("Added to plan", isPresented: .init(get: { addedMealName != nil }, set: { if !$0 { addedMealName = nil } })) {
            Button("OK") { addedMealName = nil }
        } message: {
            Text("\(addedMealName ?? "") is on the plan and its ingredients are on the shopping list.")
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
