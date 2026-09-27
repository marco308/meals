import CoreLocation
import XCTest
@testable import Meals

/// The store you're standing in (Q25): which one matches a fix, and how the
/// list sorts for it on this phone alone.
@MainActor
final class StoreHereTests: XCTestCase {
    private var tempDir: URL!

    // Two Hove shops ~400 m apart, one walk each: 🥩 first, or 🥬 first.
    private let hove = Supermarket(
        id: UUID(), name: "Sainsbury's Hove", aisleOrder: ["🥩", "🥬", "❓"], isActive: false,
        latitude: 50.8305, longitude: -0.1712, radiusM: 150
    )
    private let tesco = Supermarket(
        id: UUID(), name: "Tesco Hove", aisleOrder: ["🥬", "🥩", "❓"], isActive: false,
        latitude: 50.8340, longitude: -0.1710, radiusM: 150
    )
    private let noLocation = Supermarket(id: UUID(), name: "Aldi", aisleOrder: ["🥩", "🥬", "❓"], isActive: false)

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        super.tearDown()
    }

    private func fix(_ latitude: Double, _ longitude: Double, accuracy: Double = 20) -> CLLocation {
        CLLocation(
            coordinate: CLLocationCoordinate2D(latitude: latitude, longitude: longitude),
            altitude: 0, horizontalAccuracy: accuracy, verticalAccuracy: -1, timestamp: .now
        )
    }

    private func makeStore(_ markets: [Supermarket]) async -> (ShoppingListStore, FakeShoppingAPI) {
        let onion = TestData.item(name: "onion", aisle: "🥬")
        let beef = TestData.item(name: "beef", aisle: "🥩")
        let api = FakeShoppingAPI(list: TestData.payload([onion, beef]))
        api.supermarkets = markets
        let store = ShoppingListStore(
            api: { api }, directory: tempDir, sleep: { _ in try await Task.sleep(for: .seconds(3600)) }
        )
        await store.sync()
        return (store, api)
    }

    // MARK: Matching

    func testTheNearestStoreWhoseRadiusTakesInTheFixMatches() {
        let markets = [hove, tesco, noLocation]
        XCTAssertEqual(StoreMatcher.store(at: fix(50.8306, -0.1713), in: markets)?.id, hove.id)
        XCTAssertEqual(StoreMatcher.store(at: fix(50.8339, -0.1711), in: markets)?.id, tesco.id)
        XCTAssertNil(StoreMatcher.store(at: fix(50.8200, -0.1712), in: markets), "a km away is nowhere")
    }

    func testOverlappingStoresGoToTheNearer() {
        var wide = tesco
        wide.radiusM = 1000
        XCTAssertEqual(StoreMatcher.store(at: fix(50.8306, -0.1713), in: [wide, hove])?.id, hove.id)
    }

    func testAVagueFixMatchesNothing() {
        XCTAssertNil(StoreMatcher.store(at: fix(50.8305, -0.1712, accuracy: 500), in: [hove]))
        XCTAssertNil(StoreMatcher.store(at: fix(50.8305, -0.1712, accuracy: -1), in: [hove]))
    }

    // MARK: Sorting on this phone

    func testStandingInAStoreSortsTheListByItsWalk() async {
        let (store, _) = await makeStore([hove, tesco])
        XCTAssertEqual(store.displayItems.map(\.name), ["onion", "beef"], "the household's order first")

        store.noteLocation(fix(50.8305, -0.1712))
        XCTAssertEqual(store.storeHere?.id, hove.id)
        XCTAssertEqual(store.displayItems.map(\.name), ["beef", "onion"], "then Sainsbury's walk")
    }

    func testTheStoresAreCachedSoMatchingWorksOffline() async {
        let (store, api) = await makeStore([hove])
        api.failWith = .offline
        await store.sync()
        store.noteLocation(fix(50.8305, -0.1712))
        XCTAssertEqual(store.storeHere?.id, hove.id)

        // And across a relaunch, from disk.
        let reopened = ShoppingListStore(api: { api }, directory: tempDir)
        reopened.noteLocation(fix(50.8305, -0.1712))
        XCTAssertEqual(reopened.storeHere?.id, hove.id)
    }

    func testTheHouseholdsActiveStoreNeedsNoBanner() async {
        let active = Supermarket(
            id: hove.id, name: hove.name, aisleOrder: hove.aisleOrder, isActive: true,
            latitude: hove.latitude, longitude: hove.longitude, radiusM: hove.radiusM
        )
        let (store, _) = await makeStore([active])
        store.noteLocation(fix(50.8305, -0.1712))
        XCTAssertNil(store.storeHere, "already sorted for it, by everybody")
    }

    func testUndoHoldsUntilThePhoneIsSomewhereElse() async {
        let (store, _) = await makeStore([hove, tesco])
        store.noteLocation(fix(50.8305, -0.1712))
        store.declineStoreHere()
        XCTAssertNil(store.storeHere)
        XCTAssertEqual(store.displayItems.map(\.name), ["onion", "beef"])

        store.noteLocation(fix(50.8306, -0.1711))
        XCTAssertNil(store.storeHere, "still here, still no")

        store.noteLocation(fix(50.8339, -0.1711))
        XCTAssertEqual(store.storeHere?.id, tesco.id, "a different shop is a fresh question")
        store.noteLocation(fix(50.8305, -0.1712))
        XCTAssertEqual(store.storeHere?.id, hove.id, "and the no was about that visit")
    }

    func testLeavingTheStoreGoesBackToTheHouseholdOrder() async {
        let (store, _) = await makeStore([hove])
        store.noteLocation(fix(50.8305, -0.1712))
        store.noteLocation(fix(50.8200, -0.1712))
        XCTAssertNil(store.storeHere)
        XCTAssertEqual(store.displayItems.map(\.name), ["onion", "beef"])
    }

    func testAVagueFixDoesNotUnsortTheList() async {
        let (store, _) = await makeStore([hove])
        store.noteLocation(fix(50.8305, -0.1712))
        store.noteLocation(fix(50.8200, -0.1712, accuracy: 800))
        XCTAssertEqual(store.storeHere?.id, hove.id)
    }

    func testChangingAccountForgetsTheMatch() async {
        let (store, _) = await makeStore([hove])
        store.noteLocation(fix(50.8305, -0.1712))
        let owner = DataOwner(server: "https://meals.example.com", userId: UUID(), householdId: UUID())
        store.accountChanged(from: .signedOut, to: AccountState(signedIn: true, owner: owner))
        store.accountChanged(from: AccountState(signedIn: true, owner: owner), to: .signedOut)
        XCTAssertNil(store.matchedStoreID)
    }

    func testAServerWithoutLocationsNeverMatches() async {
        let (store, _) = await makeStore([noLocation])
        XCTAssertTrue(store.locatedStores.isEmpty)
        store.noteLocation(fix(50.8305, -0.1712))
        XCTAssertNil(store.storeHere)
    }

    func testAnOldServersReplyStillDecodes() throws {
        let json = #"[{"id": "\#(UUID())", "name": "Aldi", "aisle_order": ["🥬"], "is_active": false, "created_at": "2026-09-27T08:00:00"}]"#
        let markets = try APIClient.decoder().decode([Supermarket].self, from: Data(json.utf8))
        XCTAssertFalse(markets[0].isLocated)
    }
}
