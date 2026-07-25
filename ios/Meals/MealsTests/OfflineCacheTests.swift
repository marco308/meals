import XCTest
@testable import Meals

/// Issue #33: the plan and the recipe library are read constantly in exactly
/// the places the shopping list is — the supermarket, and a kitchen with the
/// worst signal in the house. A cold launch offline used to show an empty app.
///
/// A URLProtocol that always fails stands in for "no signal"; APIClient maps
/// any transport failure to `.offline`.
final class AlwaysOfflineProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet))
    }
    override func stopLoading() {}
}

@MainActor
final class OfflineCacheTests: XCTestCase {
    private var directory: URL!

    override func setUp() {
        super.setUp()
        directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: directory)
        super.tearDown()
    }

    private func offlineClient() -> APIClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [AlwaysOfflineProtocol.self]
        return APIClient(
            baseURL: URL(string: "http://testserver")!,
            token: "meals_test-token",
            session: URLSession(configuration: config)
        )
    }

    func testDiskCacheRoundTripsAndClears() {
        let cache = DiskCache<[String]>(directory: directory, filename: "things.json")
        XCTAssertNil(cache.load())
        cache.save(["a", "b"])
        XCTAssertEqual(cache.load(), ["a", "b"])
        cache.clear()
        XCTAssertNil(cache.load())
    }

    func testPlanStoreServesTheCachedPlanWhenOffline() async {
        let plan = Plan(
            id: UUID(), label: "w/c 20 July", status: "active", meals: []
        )
        DiskCache<Plan>(directory: directory, filename: "plan-cache.json").save(plan)

        let store = PlanStore(api: { self.offlineClient() }, directory: directory)
        XCTAssertEqual(store.plan?.label, "w/c 20 July", "cold launch reads the cache before any request")

        await store.refresh()
        XCTAssertEqual(store.plan?.label, "w/c 20 July", "a failed refresh must not blank the plan")
        XCTAssertTrue(store.isOffline)
        XCTAssertNil(store.errorMessage, "a usable cached plan isn't an error to report")
    }

    func testPlanStoreSaysSoWhenThereIsNothingCached() async {
        let store = PlanStore(api: { self.offlineClient() }, directory: directory)
        await store.refresh()
        XCTAssertTrue(store.isOffline)
        XCTAssertNotNil(store.errorMessage)
        XCTAssertFalse(
            store.errorMessage!.contains("sync"),
            "only the shopping list has a queue — nothing here can promise a sync"
        )
    }

    func testRecipeStoreKeepsTheLibraryAndKnowsItIsStale() async {
        let recipes = [
            RecipeSummary(
                id: UUID(), title: "Cottage Pie", sourceUrl: nil, servings: 4,
                prepMinutes: nil, cookMinutes: nil, tags: []
            )
        ]
        DiskCache<[RecipeSummary]>(directory: directory, filename: "recipe-library-cache.json").save(recipes)

        let store = RecipeStore(api: { self.offlineClient() }, directory: directory)
        XCTAssertEqual(store.recipes.count, 1)

        await store.refresh()
        XCTAssertEqual(store.recipes.count, 1, "'No recipes yet' must never mean 'the fetch failed'")
        XCTAssertTrue(store.isOffline)
    }

    func testRecipeDetailIsReadableOfflineOnceSeen() async throws {
        let id = UUID()
        let recipe = Recipe(
            id: id, title: "Cottage Pie", sourceUrl: nil, servings: 4, prepMinutes: nil,
            cookMinutes: nil, imageUrl: nil, instructions: "Peel the potatoes.", tags: [],
            parseSource: "jsonld", edited: false, ingredients: []
        )
        DiskCache<[UUID: Recipe]>(directory: directory, filename: "recipe-detail-cache.json").save([id: recipe])

        let store = RecipeStore(api: { self.offlineClient() }, directory: directory)
        let loaded = try await store.detail(id: id)
        XCTAssertEqual(loaded.instructions, "Peel the potatoes.", "the method is the thing you need in the kitchen")
        XCTAssertTrue(store.isOffline)
    }

    func testUnseenRecipeOfflineStillThrows() async {
        let store = RecipeStore(api: { self.offlineClient() }, directory: directory)
        do {
            _ = try await store.detail(id: UUID())
            XCTFail("a recipe never opened online has nothing to fall back to")
        } catch {
            XCTAssertEqual(error as? APIError, .offline)
        }
    }

    func testClearCacheWipesDiskSoAStalePlanCannotOutliveTheSession() async {
        let plan = Plan(id: UUID(), label: "w/c 20 July", status: "active", meals: [])
        DiskCache<Plan>(directory: directory, filename: "plan-cache.json").save(plan)

        let store = PlanStore(api: { self.offlineClient() }, directory: directory)
        store.clearCache()
        XCTAssertNil(store.plan)

        let afterLogout = PlanStore(api: { self.offlineClient() }, directory: directory)
        XCTAssertNil(afterLogout.plan, "logging out must not leave the last household's plan on disk")
    }
}
