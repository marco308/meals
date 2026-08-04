import Foundation
import Observation

/// The plan and meal library. Writes need a connection; **reads** fall back to
/// a disk cache (issue #33) so a cold launch in a kitchen with no signal still
/// shows the plan instead of an empty app. `isOffline` is what the views use to
/// mark the data as a saved copy — stale is useful, stale-and-unlabelled isn't.
@MainActor
@Observable
final class PlanStore {
    private(set) var plan: Plan?
    private(set) var mealLibrary: [Meal] = []
    private(set) var isLoading = false
    /// True when the last read fell back to cache.
    private(set) var isOffline = false
    var errorMessage: String?

    private let api: () -> APIClient
    private let planCache: DiskCache<Plan>
    private let mealCache: DiskCache<[Meal]>

    init(api: @escaping () -> APIClient, directory: URL? = nil) {
        self.api = api
        let base = directory ?? DiskCache<Plan>.defaultDirectory()
        self.planCache = DiskCache(directory: base, filename: "plan-cache.json")
        self.mealCache = DiskCache(directory: base, filename: "meal-library-cache.json")
        self.plan = planCache.load()
        self.mealLibrary = mealCache.load() ?? []
    }

    func refresh() async {
        isLoading = plan == nil
        defer { isLoading = false }
        do {
            plan = try await api().currentPlan()
            isOffline = false
            if let plan { planCache.save(plan) }
        } catch let error as APIError where error.isConnectivity {
            // Keep serving the cached plan; don't claim anything was saved —
            // only the shopping list can honestly promise a sync.
            isOffline = true
            if plan == nil { errorMessage = "You're offline and there's no saved copy of the plan yet." }
        } catch APIError.server(404, _) {
            plan = nil  // no active plan yet
            isOffline = false
            planCache.clear()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func loadMealLibrary() async {
        do {
            mealLibrary = try await api().meals()
            isOffline = false
            mealCache.save(mealLibrary)
        } catch let error as APIError where error.isConnectivity {
            isOffline = true
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Drop everything cached on disk. A stale plan must not outlive the
    /// session that fetched it.
    func clearCache() {
        planCache.clear()
        mealCache.clear()
        plan = nil
        mealLibrary = []
    }

    func createPlan(label: String, copyFrom: UUID? = nil) async {
        do {
            plan = try await api().createPlan(label: label, copyFrom: copyFrom)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private(set) var history: [PlanSummary] = []

    func loadHistory() async {
        do {
            history = try await api().plans()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Rename the current plan. The label is how a week is remembered in the
    /// history, so it's worth getting right; nothing else changes.
    func renamePlan(to label: String) async {
        guard let plan else { return }
        do {
            let updated = try await api().renamePlan(id: plan.id, label: label)
            self.plan = updated
            planCache.save(updated)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Archive the current plan (Q4). Its meals' contributions come off the
    /// active shopping list server-side.
    func archiveCurrentPlan() async {
        guard let plan else { return }
        do {
            _ = try await api().archivePlan(id: plan.id)
            self.plan = nil
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// The label a plan gets when one is started implicitly. Same default
    /// `NewPlanSheet` offers, so both routes into a first plan agree.
    nonisolated static let implicitPlanLabel = "This week's options"

    /// Add a meal to the plan, starting a plan first if the household hasn't
    /// got one. Returns false when nothing landed, with `errorMessage` set:
    /// this used to `guard let plan else { return }`, which let the recipe
    /// screen announce "Added to plan" after doing precisely nothing.
    @discardableResult
    func addMeal(_ meal: Meal) async -> Bool {
        do {
            let target = try await planForWriting()
            try await put(meal, on: target)
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    /// One recipe onto the plan as a meal of its own: the one-tap path from the
    /// recipe library. The plan is resolved *before* the meal is created, so a
    /// failure can't leave an unattached meal behind in the library.
    func addRecipe(_ recipe: Recipe) async -> Meal? {
        do {
            let target = try await planForWriting()
            guard let meal = await createMeal(name: recipe.title, slot: "dinner", recipeIds: [recipe.id]) else {
                return nil
            }
            try await put(meal, on: target)
            return meal
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    private func put(_ meal: Meal, on target: Plan) async throws {
        let updated = try await api().addMeal(planId: target.id, mealId: meal.id)
        plan = updated
        isOffline = false
        planCache.save(updated)
    }

    /// The plan a write should land on. `plan` can't answer this on its own:
    /// nil means both "no active plan" and "not fetched yet", and a cached copy
    /// may have been archived from another device. So the server decides, and
    /// only a real 404 starts a new plan.
    private func planForWriting() async throws -> Plan {
        do {
            return try await api().currentPlan()
        } catch APIError.server(404, _) {
            let created = try await api().createPlan(label: Self.implicitPlanLabel)
            plan = created
            isOffline = false
            planCache.save(created)
            return created
        }
    }

    func removeMeal(_ planMeal: PlanMeal) async {
        guard let plan else { return }
        do {
            self.plan = try await api().removeMeal(planId: plan.id, planMealId: planMeal.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func markCooked(_ planMeal: PlanMeal) async {
        guard let plan else { return }
        do {
            self.plan = try await api().markCooked(planId: plan.id, planMealId: planMeal.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func createMeal(
        name: String,
        slot: String?,
        recipeIds: [UUID],
        scales: [UUID: Double] = [:],
        looseIngredients: [LooseLine] = []
    ) async -> Meal? {
        do {
            let meal = try await api().createMeal(
                name: name, slot: slot, recipeIds: recipeIds, scales: scales, looseIngredients: looseIngredients
            )
            mealLibrary.append(meal)
            return meal
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    /// Edit a meal in place (issue #16). The plan is re-fetched so the caller
    /// sees the new composition; the shopping list is re-synced server-side and
    /// the caller refreshes its store — the recalculated list is the point.
    func updateMeal(
        _ meal: Meal,
        name: String? = nil,
        slot: String? = nil,
        recipeIds: [UUID]? = nil,
        scales: [UUID: Double] = [:],
        looseIngredients: [LooseLine]? = nil
    ) async -> Meal? {
        do {
            let updated = try await api().updateMeal(
                id: meal.id,
                name: name,
                slot: slot,
                recipeIds: recipeIds,
                scales: scales,
                looseIngredients: looseIngredients
            )
            if let index = mealLibrary.firstIndex(where: { $0.id == updated.id }) {
                mealLibrary[index] = updated
            }
            await refresh()
            return updated
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    /// Delete a meal outright. The API takes it off any active plan and off the
    /// shopping list first; the cooked history survives.
    func deleteMeal(_ meal: Meal) async -> Bool {
        do {
            try await api().deleteMeal(id: meal.id)
            mealLibrary.removeAll { $0.id == meal.id }
            await refresh()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }
}

@MainActor
@Observable
final class RecipeStore {
    private(set) var recipes: [RecipeSummary] = []
    private(set) var isLoading = false
    /// True when the last read fell back to cache — so "no recipes yet" is
    /// never shown because a fetch failed (#33).
    private(set) var isOffline = false
    var errorMessage: String?
    var sort: RecipeSort = .title
    /// Library filters (mirroring the web app's): a tag chip and the
    /// under-30-minutes toggle. State lives here so every caller of
    /// `refresh()` respects them without threading parameters around.
    var tag: String? = nil
    var under30 = false

    private let api: () -> APIClient
    private let libraryCache: DiskCache<[RecipeSummary]>
    private let detailCache: DiskCache<[UUID: Recipe]>
    /// Recipes opened at least once, kept so the method is readable in a
    /// kitchen with no signal.
    private var details: [UUID: Recipe] = [:]

    init(api: @escaping () -> APIClient, directory: URL? = nil) {
        self.api = api
        let base = directory ?? DiskCache<Plan>.defaultDirectory()
        self.libraryCache = DiskCache(directory: base, filename: "recipe-library-cache.json")
        self.detailCache = DiskCache(directory: base, filename: "recipe-detail-cache.json")
        self.recipes = libraryCache.load() ?? []
        self.details = detailCache.load() ?? [:]
    }

    func refresh(search: String? = nil) async {
        isLoading = recipes.isEmpty
        defer { isLoading = false }
        do {
            recipes = try await api().recipes(
                search: search, sort: sort, tag: tag, maxTotalMinutes: under30 ? 30 : nil
            )
            isOffline = false
            // Only cache the unfiltered library — a search result isn't the
            // library, and restoring one on next launch would look like data
            // loss.
            if (search == nil || search!.isEmpty) && tag == nil && !under30 { libraryCache.save(recipes) }
        } catch let error as APIError where error.isConnectivity {
            isOffline = true
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// A recipe in full, from the server when it can be reached and from the
    /// last-seen copy when it can't — a recipe you've opened once stays
    /// readable in a kitchen with no signal (#33).
    func detail(id: UUID) async throws -> Recipe {
        do {
            let recipe = try await api().recipe(id: id)
            isOffline = false
            details[id] = recipe
            detailCache.save(details)
            return recipe
        } catch let error as APIError where error.isConnectivity {
            guard let cached = details[id] else { throw error }
            isOffline = true
            return cached
        }
    }

    /// Save a correction to a parsed recipe (#29). `ingredients` is only sent
    /// when a line actually changed — that's what decides whether the server
    /// re-syncs every meal using the recipe.
    func update(
        id: UUID,
        title: String? = nil,
        servings: Int?? = nil,
        prepMinutes: Int?? = nil,
        cookMinutes: Int?? = nil,
        imageUrl: String?? = nil,
        instructions: String?? = nil,
        tags: [String]? = nil,
        ingredients: [LooseLine]? = nil
    ) async throws -> Recipe {
        let updated = try await api().updateRecipe(
            id: id,
            title: title,
            servings: servings,
            prepMinutes: prepMinutes,
            cookMinutes: cookMinutes,
            imageUrl: imageUrl,
            instructions: instructions,
            tags: tags,
            ingredients: ingredients
        )
        details[id] = updated
        detailCache.save(details)
        await refresh()
        return updated
    }

    func clearCache() {
        libraryCache.clear()
        detailCache.clear()
        recipes = []
        details = [:]
    }

    /// Delete a recipe from the library (issue #14). Returns nil on success or
    /// a message to show: a 409 means meals still use it, and the user needs to
    /// know *which* ones, not just that some exist. The row disappears
    /// immediately and comes back if the server refuses.
    func delete(id: UUID) async -> String? {
        let snapshot = recipes
        recipes.removeAll { $0.id == id }
        do {
            try await api().deleteRecipe(id: id)
            return nil
        } catch APIError.server(409, let detail) {
            recipes = snapshot
            return await inUseMessage(recipeId: id) ?? detail
        } catch {
            recipes = snapshot
            return error.localizedDescription
        }
    }

    /// Names the meals blocking a delete. Best-effort: if the lookup itself
    /// fails the caller falls back to the API's own wording.
    private func inUseMessage(recipeId: UUID) async -> String? {
        guard let meals = try? await api().meals() else { return nil }
        let users = meals.filter { $0.recipes.contains { $0.id == recipeId } }.map(\.name)
        guard !users.isEmpty else { return nil }
        let list = users.joined(separator: ", ")
        return users.count == 1
            ? "'\(list)' still uses this recipe. Remove it from that meal first, then delete the recipe."
            : "These meals still use this recipe: \(list). Remove it from them first, then delete the recipe."
    }

    func ingest(url: String) async throws -> IngestResponse {
        let result = try await api().ingest(url: url)
        await refresh()
        return result
    }
}
