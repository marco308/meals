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

    func createPlan(label: String) async {
        do {
            plan = try await api().createPlan(label: label)
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

    func createMeal(name: String, slot: String?, recipeIds: [UUID]) async -> Meal? {
        do {
            let meal = try await api().createMeal(name: name, slot: slot, recipeIds: recipeIds)
            mealLibrary.append(meal)
            return meal
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }
}

@MainActor
@Observable
final class RecipeStore {
    private(set) var recipes: [RecipeSummary] = []
    private(set) var isLoading = false
    var errorMessage: String?

    private let api: () -> APIClient

    init(api: @escaping () -> APIClient) {
        self.api = api
    }

    func refresh(search: String? = nil) async {
        isLoading = recipes.isEmpty
        defer { isLoading = false }
        do {
            recipes = try await api().recipes(search: search)
        } catch {
            errorMessage = error.localizedDescription
        }
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
