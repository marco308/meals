import Foundation
import Observation

/// Online-only stores for the plan and recipe library (decision Q11: only
/// the shopping list needs offline support in v1).
@MainActor
@Observable
final class PlanStore {
    private(set) var plan: Plan?
    private(set) var mealLibrary: [Meal] = []
    private(set) var isLoading = false
    var errorMessage: String?

    private let api: () -> APIClient

    init(api: @escaping () -> APIClient) {
        self.api = api
    }

    func refresh() async {
        isLoading = plan == nil
        defer { isLoading = false }
        do {
            plan = try await api().currentPlan()
        } catch let error as APIError where error == .offline {
            errorMessage = APIError.offline.errorDescription
        } catch APIError.server(404, _) {
            plan = nil  // no active plan yet
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func loadMealLibrary() async {
        do {
            mealLibrary = try await api().meals()
        } catch {
            errorMessage = error.localizedDescription
        }
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

    func addMeal(_ meal: Meal) async {
        guard let plan else { return }
        do {
            self.plan = try await api().addMeal(planId: plan.id, mealId: meal.id)
        } catch {
            errorMessage = error.localizedDescription
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
        name: String, slot: String?, recipeIds: [UUID], looseIngredients: [LooseLine] = []
    ) async -> Meal? {
        do {
            let meal = try await api().createMeal(
                name: name, slot: slot, recipeIds: recipeIds, looseIngredients: looseIngredients
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
        looseIngredients: [LooseLine]? = nil
    ) async -> Meal? {
        do {
            let updated = try await api().updateMeal(
                id: meal.id, name: name, slot: slot, recipeIds: recipeIds, looseIngredients: looseIngredients
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
    var errorMessage: String?
    var sort: RecipeSort = .title

    private let api: () -> APIClient

    init(api: @escaping () -> APIClient) {
        self.api = api
    }

    func refresh(search: String? = nil) async {
        isLoading = recipes.isEmpty
        defer { isLoading = false }
        do {
            recipes = try await api().recipes(search: search, sort: sort)
        } catch {
            errorMessage = error.localizedDescription
        }
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

    func detail(id: UUID) async throws -> Recipe {
        try await api().recipe(id: id)
    }

    func ingest(url: String) async throws -> IngestResponse {
        let result = try await api().ingest(url: url)
        await refresh()
        return result
    }
}
