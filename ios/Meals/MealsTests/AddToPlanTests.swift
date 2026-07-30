import XCTest
@testable import Meals

/// Canned server responses. Deliberately outside the (main-actor) test case so
/// the `StubProtocol` handler, which runs on URLSession's thread, can reach them.
private enum Canned {
    static let planId = UUID(uuidString: "1E4E9C1E-0A8E-4C60-9C2A-8E4E2B7D9C31")!
    static let mealId = UUID(uuidString: "61931D47-4154-418A-A43F-F734A0E3D888")!
    static let planMealsPath = "/plans/\(planId.uuidString.lowercased())/meals"
    static let noActivePlan = Data(#"{"detail": "no active plan; create one via POST /plans"}"#.utf8)
    static let unexpected = Data(#"{"detail": "unexpected request"}"#.utf8)

    static let mealBody =
        #"{"id": "\#(mealId.uuidString.lowercased())", "name": "Cottage Pie", "slot": "dinner", "recipes": [], "loose_ingredients": []}"#

    static var mealJSON: Data { Data(mealBody.utf8) }

    static func planJSON(label: String, meals: String = "[]") -> Data {
        Data(
            #"{"id": "\#(planId.uuidString.lowercased())", "label": "\#(label)", "status": "active", "meals": \#(meals)}"#
            .utf8
        )
    }

    /// The plan as `POST /plans/{id}/meals` returns it: one meal on board.
    static var plannedPlanJSON: Data {
        let planMeal = #"{"id": "d2b1a9c4-1e2f-4a3b-8c5d-6e7f8a9b0c1d", "cooked_at": null, "meal": \#(mealBody)}"#
        return planJSON(label: PlanStore.implicitPlanLabel, meals: "[\(planMeal)]")
    }

    static var recipe: Recipe {
        Recipe(
            id: UUID(), title: "Cottage Pie", sourceUrl: nil, servings: 4, prepMinutes: nil,
            cookMinutes: nil, imageUrl: nil, instructions: nil, tags: [],
            parseSource: "jsonld", edited: false, ingredients: []
        )
    }

    static var meal: Meal {
        Meal(id: mealId, name: "Cottage Pie", slot: "dinner", recipes: [], looseIngredients: [])
    }
}

/// Every request the store made, so a test can assert what the server was (and
/// wasn't) asked to do.
private final class Calls: @unchecked Sendable {
    private let lock = NSLock()
    private var paths: [String] = []

    func record(_ request: URLRequest) {
        lock.lock()
        paths.append("\(request.httpMethod ?? "?") \(request.url?.path ?? "")")
        lock.unlock()
    }

    var seen: [String] {
        lock.lock()
        defer { lock.unlock() }
        return paths
    }
}

/// "Add to this week's plan" on a recipe used to report success and do nothing
/// whenever the household had no active plan: `PlanStore.addMeal` bailed out on
/// `guard let plan`, and the recipe screen showed "Added to plan" regardless.
/// A tap that says it worked has to have worked.
@MainActor
final class AddToPlanTests: XCTestCase {
    private var directory: URL!

    override func setUp() {
        super.setUp()
        directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    }

    override func tearDown() {
        StubProtocol.handler = nil
        try? FileManager.default.removeItem(at: directory)
        super.tearDown()
    }

    private func store(protocolClass: AnyClass = StubProtocol.self) -> PlanStore {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [protocolClass]
        let api = APIClient(
            baseURL: URL(string: "http://testserver")!,
            token: "meals_test-token",
            session: URLSession(configuration: config)
        )
        return PlanStore(api: { api }, directory: directory)
    }

    /// The bug: no active plan, so one gets started and the meal lands on it.
    func testAddingARecipeWithNoActivePlanStartsOneAndAddsTheMeal() async {
        let calls = Calls()
        StubProtocol.handler = { request in
            calls.record(request)
            switch (request.httpMethod ?? "", request.url?.path ?? "") {
            case ("GET", "/plans/current"):
                return (404, Canned.noActivePlan)
            case ("POST", "/plans"):
                return (201, Canned.planJSON(label: PlanStore.implicitPlanLabel))
            case ("POST", "/meals"):
                return (201, Canned.mealJSON)
            case ("POST", Canned.planMealsPath):
                return (201, Canned.plannedPlanJSON)
            default:
                return (500, Canned.unexpected)
            }
        }

        let store = self.store()
        let added = await store.addRecipe(Canned.recipe)

        XCTAssertEqual(added?.name, "Cottage Pie")
        XCTAssertNil(store.errorMessage)
        XCTAssertEqual(store.plan?.meals.count, 1, "the meal has to be on the plan the store now holds")
        XCTAssertEqual(store.plan?.label, PlanStore.implicitPlanLabel)
        XCTAssertTrue(calls.seen.contains("POST /plans"), "a missing plan is started, not silently skipped")
        XCTAssertTrue(calls.seen.contains("POST \(Canned.planMealsPath)"))
    }

    /// A plan already on the server is used as-is: a nil `plan` also means
    /// "Recipes was opened before Plan", and starting a second active plan
    /// there would split the week across two plans.
    func testAddingDoesNotStartASecondPlanWhenTheServerHasOne() async {
        let calls = Calls()
        StubProtocol.handler = { request in
            calls.record(request)
            switch (request.httpMethod ?? "", request.url?.path ?? "") {
            case ("GET", "/plans/current"):
                return (200, Canned.planJSON(label: "w/c 27 July"))
            case ("POST", "/meals"):
                return (201, Canned.mealJSON)
            case ("POST", Canned.planMealsPath):
                return (201, Canned.plannedPlanJSON)
            default:
                return (500, Canned.unexpected)
            }
        }

        let store = self.store()
        XCTAssertNil(store.plan, "nothing cached and nothing fetched: the ambiguous case")
        let added = await store.addRecipe(Canned.recipe)

        XCTAssertNotNil(added)
        XCTAssertFalse(calls.seen.contains("POST /plans"), "the household's existing plan is the one to add to")
    }

    /// `addMeal`, the plan tab's own route, gets the same treatment and says
    /// whether it worked.
    func testAddMealStartsAPlanAndReportsSuccess() async {
        StubProtocol.handler = { request in
            switch (request.httpMethod ?? "", request.url?.path ?? "") {
            case ("GET", "/plans/current"):
                return (404, Canned.noActivePlan)
            case ("POST", "/plans"):
                return (201, Canned.planJSON(label: PlanStore.implicitPlanLabel))
            case ("POST", Canned.planMealsPath):
                return (201, Canned.plannedPlanJSON)
            default:
                return (500, Canned.unexpected)
            }
        }

        let store = self.store()
        let added = await store.addMeal(Canned.meal)

        XCTAssertTrue(added)
        XCTAssertEqual(store.plan?.meals.count, 1)
    }

    /// Offline, nothing lands, and the caller is told rather than left to
    /// announce a phantom add.
    func testAddingOfflineFailsLoudly() async {
        let store = self.store(protocolClass: AlwaysOfflineProtocol.self)

        let added = await store.addMeal(Canned.meal)
        XCTAssertFalse(added)
        XCTAssertNotNil(store.errorMessage)

        let recipeAdd = await store.addRecipe(Canned.recipe)
        XCTAssertNil(recipeAdd)
    }

    /// The plan is resolved before the meal is created, so a failure can't
    /// leave an orphan meal in the library.
    func testARecipeIsNotTurnedIntoAMealWhenNoPlanCanBeResolved() async {
        let calls = Calls()
        StubProtocol.handler = { request in
            calls.record(request)
            return (500, Data(#"{"detail": "boom"}"#.utf8))
        }

        let store = self.store()
        let added = await store.addRecipe(Canned.recipe)
        XCTAssertNil(added)
        XCTAssertFalse(calls.seen.contains("POST /meals"), "no plan to add to means no meal to strand")
        XCTAssertNotNil(store.errorMessage)
    }
}
