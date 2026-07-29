import XCTest
@testable import Meals

/// Configurable fake backend for the offline store. Runs on the main actor,
/// same as the store, so recorded calls are data-race free.
@MainActor
final class FakeShoppingAPI: ShoppingAPI {
    var list: ShoppingListPayload
    var aisles: [Aisle] = [Aisle(emoji: "🥬", label: "Fruit & veg"), Aisle(emoji: "🥩", label: "Meat & fish"), Aisle(emoji: "❓", label: "Unknown")]
    var failWith: APIError?
    var addItemResult: ((AdhocPayload) -> ListItem)?
    var patchFailures: [UUID: APIError] = [:]

    private(set) var patches: [(id: UUID, checked: Bool?, excluded: Bool?, stapleNeeded: Bool?)] = []
    private(set) var added: [AdhocPayload] = []
    private(set) var fetchCount = 0

    init(list: ShoppingListPayload) {
        self.list = list
    }

    func fetchList() async throws -> ShoppingListPayload {
        if let failWith { throw failWith }
        fetchCount += 1
        return list
    }

    func fetchAisles() async throws -> [Aisle] {
        if let failWith { throw failWith }
        return aisles
    }

    func patchItem(id: UUID, checked: Bool?, excluded: Bool?, stapleNeeded: Bool?) async throws -> ListItem {
        if let failWith { throw failWith }
        if let failure = patchFailures[id] { throw failure }
        patches.append((id, checked, excluded, stapleNeeded))
        var item = list.items.first { $0.id == id } ?? TestData.item(id: id, name: "unknown")
        if let checked { item.checked = checked }
        if let excluded { item.excluded = excluded }
        if let stapleNeeded { item.stapleNeeded = stapleNeeded }
        return item
    }

    func addItem(_ payload: AdhocPayload) async throws -> ListItem {
        if let failWith { throw failWith }
        added.append(payload)
        if let addItemResult { return addItemResult(payload) }
        return TestData.item(id: payload.id, name: payload.name, quantity: payload.quantity, unit: payload.unit)
    }

    func archiveList() async throws {
        if let failWith { throw failWith }
    }
}

enum TestData {
    static func item(
        id: UUID = UUID(),
        name: String,
        aisle: String = "🥬",
        isStaple: Bool = false,
        quantity: Double? = 1,
        unit: String? = "item",
        checked: Bool = false,
        excluded: Bool = false,
        stapleNeeded: Bool? = nil,
        sources: [ItemSource] = []
    ) -> ListItem {
        ListItem(
            id: id, ingredientId: id, name: name, aisle: aisle, aisleLabel: "Aisle", isStaple: isStaple,
            quantity: quantity, unit: unit, display: ShoppingListStore.displayQuantity(quantity, unit),
            checked: checked, excluded: excluded, stapleNeeded: stapleNeeded, sources: sources
        )
    }

    static func payload(_ items: [ListItem]) -> ShoppingListPayload {
        ShoppingListPayload(id: UUID(), status: "active", items: items, hiddenStaples: 0)
    }
}

@MainActor
final class ShoppingListStoreTests: XCTestCase {
    private var tempDir: URL!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        super.tearDown()
    }

    private func makeStore(_ api: FakeShoppingAPI) -> ShoppingListStore {
        ShoppingListStore(api: { api }, directory: tempDir)
    }

    // MARK: Offline behaviour (the Q11 hard requirement)

    func testCheckOffWorksOfflineAndQueues() async {
        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let store = makeStore(api)
        await store.sync()  // prime the cache while "online"

        api.failWith = .offline
        store.toggleChecked(onion)

        XCTAssertEqual(store.pending.count, 1)
        XCTAssertTrue(store.displayItems.isEmpty, "check-off must render instantly with no network")
        XCTAssertTrue(store.checkedItems.first!.checked)

        await store.sync()
        XCTAssertTrue(store.isOffline)
        XCTAssertEqual(store.pending.count, 1, "op stays queued while offline")
        XCTAssertTrue(store.displayItems.isEmpty)
        XCTAssertEqual(store.checkedItems.map(\.name), ["onion"])
    }

    func testAdhocAddWorksOfflineAndMergesInProjection() async {
        let milk = TestData.item(name: "milk", quantity: 1000, unit: "ml")
        let api = FakeShoppingAPI(list: TestData.payload([milk]))
        let store = makeStore(api)
        await store.sync()

        api.failWith = .offline
        store.addAdhoc(name: "Milk", quantity: 1000, unit: "ml")
        store.addAdhoc(name: "bin bags", quantity: nil, unit: nil)
        await store.sync()

        let displayed = store.displayItems
        let milkRow = displayed.first { $0.name == "milk" }
        XCTAssertEqual(milkRow?.quantity, 2000, "same name+unit merges in the offline projection")
        XCTAssertEqual(milkRow?.display, "2 l")
        XCTAssertTrue(displayed.contains { $0.name == "bin bags" }, "brand-new item appears offline")
    }

    func testUnitMismatchStaysSeparateOffline() async {
        let byCount = TestData.item(name: "onion", quantity: 2, unit: "item")
        let api = FakeShoppingAPI(list: TestData.payload([byCount]))
        let store = makeStore(api)
        await store.sync()

        api.failWith = .offline
        store.addAdhoc(name: "onion", quantity: 500, unit: "g")
        let onions = store.displayItems.filter { $0.name == "onion" }
        XCTAssertEqual(onions.count, 2, "exact-unit merging only — g and item stay separate lines")
    }

    func testQueueSurvivesRelaunch() async {
        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let first = makeStore(api)
        await first.sync()
        api.failWith = .offline
        first.toggleChecked(onion)
        first.addAdhoc(name: "milk", quantity: 2000, unit: "ml")
        await first.sync()
        XCTAssertEqual(first.pending.count, 2)

        // "Relaunch": a fresh store over the same directory
        let second = makeStore(api)
        XCTAssertEqual(second.pending.count, 2, "queued ops persist across launches")
        XCTAssertEqual(second.cache?.payload.items.count, 1, "cached list persists across launches")
        XCTAssertTrue(second.checkedItems.first { $0.name == "onion" }!.checked, "projection applies after relaunch")
    }

    // MARK: Sync / replay

    func testSyncReplaysInOrderThenRefetches() async {
        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let store = makeStore(api)
        await store.sync()

        api.failWith = .offline
        store.toggleChecked(onion)                       // checked = true
        store.addAdhoc(name: "milk", quantity: 500, unit: "ml")
        await store.sync()
        XCTAssertEqual(store.pending.count, 2)

        api.failWith = nil
        await store.sync()

        XCTAssertTrue(store.pending.isEmpty, "queue drains once back online")
        XCTAssertFalse(store.isOffline)
        XCTAssertEqual(api.patches.count, 1)
        XCTAssertEqual(api.patches.first?.checked, true)
        XCTAssertEqual(api.added.count, 1)
        XCTAssertEqual(api.added.first?.name, "milk")
    }

    func testAdhocClientIdIsSentForIdempotency() async {
        let api = FakeShoppingAPI(list: TestData.payload([]))
        let store = makeStore(api)
        store.addAdhoc(name: "milk", quantity: 500, unit: "ml")
        await store.sync()
        let sent = api.added.first
        XCTAssertNotNil(sent?.id, "client-generated id makes replays safe")
    }

    func testServerMergeRemapsFollowUpOps() async {
        // Offline: add "milk" (synthetic id), then check it off. Online, the
        // server merges the add into an EXISTING line with a different id —
        // the queued check-off must follow it there.
        let serverMilkId = UUID()
        let api = FakeShoppingAPI(list: TestData.payload([]))
        api.failWith = .offline
        let store = makeStore(api)
        store.addAdhoc(name: "milk", quantity: 500, unit: "ml")
        await store.sync()
        let synthetic = store.displayItems.first { $0.name == "milk" }!
        store.toggleChecked(synthetic)
        XCTAssertEqual(store.pending.count, 2)

        api.failWith = nil
        api.addItemResult = { _ in TestData.item(id: serverMilkId, name: "milk", quantity: 1500, unit: "ml") }
        await store.sync()

        XCTAssertEqual(api.patches.count, 1)
        XCTAssertEqual(api.patches.first?.id, serverMilkId, "op re-targeted to the server's merged item id")
    }

    func testRejectedOpIsDroppedAndSyncContinues() async {
        let ghost = TestData.item(name: "ghost")
        let real = TestData.item(name: "real")
        let api = FakeShoppingAPI(list: TestData.payload([ghost, real]))
        let store = makeStore(api)
        await store.sync()

        api.failWith = .offline
        store.toggleChecked(ghost)
        store.toggleChecked(real)
        await store.sync()

        api.failWith = nil
        api.patchFailures[ghost.id] = .server(status: 404, detail: "list item not found")
        await store.sync()

        XCTAssertTrue(store.pending.isEmpty, "a 4xx op is dropped, not wedged")
        XCTAssertEqual(api.patches.map(\.id), [real.id], "later ops still replay")
    }

    // MARK: Filtering & grouping

    func testStaplesHiddenUntilStaplesCheck() async {
        let oil = TestData.item(name: "olive oil", isStaple: true)
        let milk = TestData.item(name: "milk")
        let api = FakeShoppingAPI(list: TestData.payload([oil, milk]))
        let store = makeStore(api)
        await store.sync()

        XCTAssertFalse(store.displayItems.contains { $0.isStaple })
        XCTAssertEqual(store.stapleCheckItems.map(\.name), ["olive oil"])

        store.includeStaples = true
        XCTAssertTrue(store.displayItems.contains { $0.name == "olive oil" })
    }

    func testStapleCheckListsOnlyStaplesMinusExclusions() async {
        let oil = TestData.item(name: "olive oil", aisle: "🥩", isStaple: true)
        let salt = TestData.item(name: "salt", aisle: "🥬", isStaple: true)
        let cupboardSalt = TestData.item(name: "sea salt", isStaple: true, excluded: true)
        let milk = TestData.item(name: "milk")
        let api = FakeShoppingAPI(list: TestData.payload([oil, salt, cupboardSalt, milk]))
        let store = makeStore(api)
        await store.sync()

        XCTAssertEqual(
            store.stapleCheckItems.map(\.name), ["salt", "olive oil"],
            "staples only, aisle-ordered, minus already-have exclusions"
        )
    }

    func testMarkStapleNeededSurfacesJustThatStapleOffline() async {
        let oil = TestData.item(name: "olive oil", aisle: "🥩", isStaple: true)
        let salt = TestData.item(name: "salt", isStaple: true)
        let api = FakeShoppingAPI(list: TestData.payload([oil, salt]))
        let store = makeStore(api)
        await store.sync()
        XCTAssertTrue(store.displayItems.isEmpty)

        api.failWith = .offline
        store.markStapleNeeded(oil)

        XCTAssertEqual(store.displayItems.map(\.name), ["olive oil"], "the marked staple joins the list, even offline")
        XCTAssertFalse(store.displayItems.contains { $0.name == "salt" }, "unmarked staples stay hidden")

        let surfaced = store.stapleCheckItems.first { $0.name == "olive oil" }!
        XCTAssertTrue(surfaced.isNeededStaple, "the check view shows the marked state")
        store.unmarkStapleNeeded(surfaced)
        XCTAssertTrue(store.displayItems.isEmpty, "have-it-after-all hides the staple again")
    }

    func testStapleNeededReplaysToServer() async {
        let oil = TestData.item(name: "olive oil", isStaple: true)
        let api = FakeShoppingAPI(list: TestData.payload([oil]))
        let store = makeStore(api)
        await store.sync()

        api.failWith = .offline
        store.markStapleNeeded(oil)
        await store.sync()
        XCTAssertEqual(store.pending.count, 1)

        api.failWith = nil
        await store.sync()
        XCTAssertTrue(store.pending.isEmpty)
        XCTAssertEqual(api.patches.count, 1)
        XCTAssertEqual(api.patches.first?.stapleNeeded, true)
        XCTAssertNil(api.patches.first?.checked)
        XCTAssertNil(api.patches.first?.excluded)
    }

    func testExcludedItemsHiddenButProjectionKeepsThem() async {
        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let store = makeStore(api)
        await store.sync()

        api.failWith = .offline
        store.markAlreadyHave(onion)
        XCTAssertFalse(store.displayItems.contains { $0.name == "onion" }, "already-have drops off this shop")
        XCTAssertEqual(store.pending.count, 1)
    }

    func testShowAlreadyHaveRevealsAndPutBackRestores() async {
        let onion = TestData.item(name: "onion", excluded: true)
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let store = makeStore(api)
        await store.sync()

        XCTAssertEqual(store.excludedCount, 1)
        XCTAssertTrue(store.displayItems.isEmpty)

        store.includeExcluded = true
        XCTAssertTrue(store.displayItems.first!.excluded, "reveal shows the excluded line")

        api.failWith = .offline
        store.putBack(onion)
        store.includeExcluded = false
        let restored = store.displayItems.first { $0.name == "onion" }
        XCTAssertNotNil(restored, "put-back returns the item to this shop, even offline")
        XCTAssertFalse(restored!.excluded)
        XCTAssertEqual(store.excludedCount, 0)
    }


    func testSectionsFollowStoreWalkingOrder() async {
        let beef = TestData.item(name: "minced beef", aisle: "🥩")
        let onion = TestData.item(name: "onion", aisle: "🥬")
        let mystery = TestData.item(name: "zzz mystery", aisle: "❓")
        let api = FakeShoppingAPI(list: TestData.payload([mystery, beef, onion]))
        let store = makeStore(api)
        await store.sync()

        XCTAssertEqual(store.sections.map(\.aisle), ["🥬", "🥩", "❓"], "veg before meat, unknown last")
    }

    func testNewContributionRevivesCheckedLine() async {
        let milk = TestData.item(name: "milk", quantity: 1000, unit: "ml", checked: true)
        let api = FakeShoppingAPI(list: TestData.payload([milk]))
        let store = makeStore(api)
        await store.sync()

        api.failWith = .offline
        store.addAdhoc(name: "milk", quantity: 1000, unit: "ml")
        XCTAssertFalse(store.displayItems.first { $0.name == "milk" }!.checked, "fresh need un-checks the line")
        XCTAssertTrue(store.checkedItems.isEmpty, "and it comes back out of the basket")
    }

    // MARK: Checked-off items

    func testCheckedItemsLeaveTheAislesButStayVisibleInTheBasket() async {
        let onion = TestData.item(name: "onion", aisle: "🥬")
        let beef = TestData.item(name: "minced beef", aisle: "🥩")
        let api = FakeShoppingAPI(list: TestData.payload([onion, beef]))
        let store = makeStore(api)
        await store.sync()

        store.toggleChecked(onion)

        XCTAssertEqual(store.displayItems.map(\.name), ["minced beef"], "checked items drop out of the aisles")
        XCTAssertFalse(store.sections.contains { $0.aisle == "🥬" }, "and take their empty section with them")
        XCTAssertEqual(store.checkedItems.map(\.name), ["onion"], "but stay reachable in the basket")
    }

    func testUncheckingReturnsTheItemToItsAisle() async {
        let onion = TestData.item(name: "onion", checked: true)
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let store = makeStore(api)
        await store.sync()
        XCTAssertEqual(store.checkedItems.count, 1)

        api.failWith = .offline
        store.toggleChecked(store.checkedItems.first!)

        XCTAssertEqual(store.displayItems.map(\.name), ["onion"], "un-check puts it back, even offline")
        XCTAssertTrue(store.checkedItems.isEmpty)
    }

    func testHiddenItemsStayOutOfTheBasket() async {
        let salt = TestData.item(name: "salt", isStaple: true, checked: true)
        let onion = TestData.item(name: "onion", checked: true, excluded: true)
        let milk = TestData.item(name: "milk", checked: true)
        let api = FakeShoppingAPI(list: TestData.payload([salt, onion, milk]))
        let store = makeStore(api)
        await store.sync()

        XCTAssertEqual(
            store.checkedItems.map(\.name), ["milk"],
            "the basket counts only what dropped out of the list — not hidden staples or already-have lines"
        )
    }
}

final class QuickAddAndDisplayTests: XCTestCase {
    func testParseQuickAddTrailingQuantity() {
        let (name, quantity, unit) = ShoppingListView.parseQuickAdd("milk 2 l")
        XCTAssertEqual(name, "milk")
        XCTAssertEqual(quantity, 2)
        XCTAssertEqual(unit, "l")
    }

    func testParseQuickAddLeadingQuantity() {
        let (name, quantity, unit) = ShoppingListView.parseQuickAdd("2 tins chopped tomatoes")
        XCTAssertEqual(name, "chopped tomatoes")
        XCTAssertEqual(quantity, 2)
        XCTAssertEqual(unit, "tins")
    }

    func testParseQuickAddPlainName() {
        let (name, quantity, unit) = ShoppingListView.parseQuickAdd("bin bags")
        XCTAssertEqual(name, "bin bags")
        XCTAssertNil(quantity)
        XCTAssertNil(unit)
    }

    func testDisplayQuantityUpgradesUnits() {
        XCTAssertEqual(ShoppingListStore.displayQuantity(1500, "g"), "1.5 kg")
        XCTAssertEqual(ShoppingListStore.displayQuantity(2000, "ml"), "2 l")
        XCTAssertEqual(ShoppingListStore.displayQuantity(500, "g"), "500 g")
        XCTAssertEqual(ShoppingListStore.displayQuantity(3, "item"), "×3")
        XCTAssertEqual(ShoppingListStore.displayQuantity(2, "tin"), "2 tins")
        XCTAssertEqual(ShoppingListStore.displayQuantity(1, "tin"), "1 tin")
        XCTAssertEqual(ShoppingListStore.displayQuantity(nil, nil), "")
    }
}
