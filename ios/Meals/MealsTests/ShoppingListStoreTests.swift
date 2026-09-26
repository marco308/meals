import XCTest
@testable import Meals

/// Configurable fake backend for the offline store. Runs on the main actor,
/// same as the store, so recorded calls are data-race free.
@MainActor
final class FakeShoppingAPI: ShoppingAPI {
    var list: ShoppingListPayload
    var aisles: [Aisle] = [Aisle(emoji: "🥬", label: "Fruit & veg"), Aisle(emoji: "🥩", label: "Meat & fish"), Aisle(emoji: "❓", label: "Unknown")]
    var failWith: APIError?
    /// Thrown by every call: for failures that aren't an `APIError` at all,
    /// like a 200 whose body won't decode.
    var failWithError: (any Error)?
    var addItemResult: ((AdhocPayload) -> ListItem)?
    var addFailure: APIError?
    var patchFailures: [UUID: APIError] = [:]
    var deleteFailures: [UUID: APIError] = [:]
    /// When set, `fetchList` waits here until it opens: a sync held mid-flight.
    var fetchGate: Gate?
    var onArchive: (() -> Void)?

    private(set) var patches: [(id: UUID, checked: Bool?, excluded: Bool?, stapleNeeded: Bool?)] = []
    private(set) var added: [AdhocPayload] = []
    private(set) var deleted: [UUID] = []
    private(set) var fetchCount = 0
    private(set) var archiveCount = 0
    /// Every call that got through, in order.
    private(set) var calls: [String] = []

    init(list: ShoppingListPayload) {
        self.list = list
    }

    private func failIfTold() throws {
        if let failWith { throw failWith }
        if let failWithError { throw failWithError }
    }

    func fetchList() async throws -> ShoppingListPayload {
        let answer = list
        if let fetchGate { await fetchGate.wait() }
        try failIfTold()
        fetchCount += 1
        calls.append("fetch")
        return answer
    }

    func fetchAisles() async throws -> [Aisle] {
        try failIfTold()
        calls.append("aisles")
        return aisles
    }

    func patchItem(id: UUID, checked: Bool?, excluded: Bool?, stapleNeeded: Bool?) async throws -> ListItem {
        try failIfTold()
        if let failure = patchFailures[id] { throw failure }
        patches.append((id, checked, excluded, stapleNeeded))
        calls.append("patch")
        var item = list.items.first { $0.id == id } ?? TestData.item(id: id, name: "unknown")
        if let checked { item.checked = checked }
        if let excluded { item.excluded = excluded }
        if let stapleNeeded { item.stapleNeeded = stapleNeeded }
        return item
    }

    func addItem(_ payload: AdhocPayload) async throws -> ListItem {
        try failIfTold()
        if let addFailure { throw addFailure }
        added.append(payload)
        calls.append("add")
        if let addItemResult { return addItemResult(payload) }
        return TestData.item(id: payload.id, name: payload.name, quantity: payload.quantity, unit: payload.unit)
    }

    func deleteItem(id: UUID) async throws {
        try failIfTold()
        if let failure = deleteFailures[id] { throw failure }
        deleted.append(id)
        calls.append("delete")
        list.items.removeAll { $0.id == id }
    }

    /// Like the server: the list is archived and the next one starts empty.
    func archiveList() async throws {
        try failIfTold()
        archiveCount += 1
        calls.append("archive")
        list = TestData.payload([])
        onArchive?()
    }
}

/// Holds whoever waits on it until the test opens it.
@MainActor
final class Gate {
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var isOpen = false

    var waiting: Int { waiters.count }

    func wait() async {
        guard !isOpen else { return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func open() {
        isOpen = true
        let woken = waiters
        waiters = []
        woken.forEach { $0.resume() }
    }
}

/// Stands in for the store's sleep between retries: records each delay asked
/// for, and lets the retry go only when the test fires it.
@MainActor
final class RetryClock {
    private(set) var requested: [Duration] = []
    private var sleepers: [CheckedContinuation<Void, Never>] = []

    func sleep(_ delay: Duration) async {
        requested.append(delay)
        await withCheckedContinuation { sleepers.append($0) }
    }

    func fire() {
        let woken = sleepers
        sleepers = []
        woken.forEach { $0.resume() }
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

    /// Retries are parked for an hour unless a test brings its own clock, so
    /// nothing fires behind a test's back.
    private func makeStore(
        _ api: FakeShoppingAPI,
        onUnauthorized: @escaping () -> Void = {},
        sleep: @escaping @Sendable (Duration) async throws -> Void = { _ in try await Task.sleep(for: .seconds(3600)) }
    ) -> ShoppingListStore {
        ShoppingListStore(api: { api }, directory: tempDir, onUnauthorized: onUnauthorized, sleep: sleep)
    }

    /// Waits (up to two seconds) for something the store does on its own
    /// time: a sync a tap started, a retry the test just fired.
    private func until(
        _ condition: () -> Bool, _ what: String = "condition", file: StaticString = #filePath, line: UInt = #line
    ) async {
        for _ in 0..<400 {
            if condition() { return }
            try? await Task.sleep(for: .milliseconds(5))
        }
        XCTFail("timed out waiting for \(what)", file: file, line: line)
    }

    private let alice = DataOwner(server: "https://meals.example.com", userId: UUID(), householdId: UUID())
    private let bob = DataOwner(server: "https://meals.example.com", userId: UUID(), householdId: UUID())

    private func signedIn(_ owner: DataOwner?) -> AccountState {
        AccountState(signedIn: true, owner: owner)
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

    // MARK: Undo the last tick

    func testUndoPutsBackTheLastTickEvenOffline() async {
        let onion = TestData.item(name: "onion")
        let milk = TestData.item(name: "milk")
        let api = FakeShoppingAPI(list: TestData.payload([onion, milk]))
        let store = makeStore(api)
        await store.sync()
        XCTAssertNil(store.lastTicked, "nothing ticked, nothing to undo")

        api.failWith = .offline
        store.toggleChecked(onion)
        store.toggleChecked(store.displayItems.first { $0.name == "milk" }!)
        XCTAssertEqual(store.lastTicked?.name, "milk")

        store.undoLastTick()
        XCTAssertEqual(store.displayItems.map(\.name), ["milk"], "the fat-fingered line is back in its aisle")
        XCTAssertEqual(store.lastTicked?.name, "onion", "and a second undo walks further back")

        store.undoLastTick()
        XCTAssertTrue(store.checkedItems.isEmpty)
        XCTAssertNil(store.lastTicked)
        store.undoLastTick()  // nothing left: a no-op, not a crash
        XCTAssertEqual(store.pending.count, 4)

        api.failWith = nil
        await store.sync()
        XCTAssertEqual(api.patches.map(\.checked), [true, true, false, false], "undo is an ordinary queued un-tick")
    }

    func testUndoSkipsLinesNoLongerTicked() async {
        let onion = TestData.item(name: "onion")
        let milk = TestData.item(name: "milk")
        let api = FakeShoppingAPI(list: TestData.payload([onion, milk]))
        let store = makeStore(api)
        await store.sync()

        api.failWith = .offline
        store.toggleChecked(onion)
        store.toggleChecked(store.displayItems.first { $0.name == "milk" }!)
        // Un-ticked by hand from the basket: undo moves on to the one before.
        store.toggleChecked(store.checkedItems.first { $0.name == "milk" }!)
        XCTAssertEqual(store.lastTicked?.name, "onion")

        // Back online, the server's list has the onion un-ticked: somebody
        // else put it back on their phone after ours landed.
        api.failWith = nil
        await store.sync()
        XCTAssertFalse(api.list.items.contains(where: \.checked))
        XCTAssertNil(store.lastTicked, "a line the server says isn't ticked is skipped, not re-sent")
    }

    func testUndoFollowsAnAddTheServerMerged() async {
        let serverMilkId = UUID()
        let api = FakeShoppingAPI(list: TestData.payload([]))
        api.failWith = .offline
        let store = makeStore(api)
        store.addAdhoc(name: "milk", quantity: 500, unit: "ml")
        store.toggleChecked(store.displayItems.first { $0.name == "milk" }!)

        api.failWith = nil
        api.addItemResult = { _ in TestData.item(id: serverMilkId, name: "milk", quantity: 500, unit: "ml") }
        api.list.items = [TestData.item(id: serverMilkId, name: "milk", quantity: 500, unit: "ml", checked: true)]
        await store.sync()

        XCTAssertEqual(store.lastTicked?.id, serverMilkId, "the tick follows its line to the server's id")
    }

    func testFinishingTheShopForgetsWhatWasTicked() async throws {
        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let store = makeStore(api)
        await store.sync()
        store.toggleChecked(onion)
        try await store.finishShop()
        XCTAssertTrue(store.tickHistory.isEmpty)
    }

    // MARK: Ad-hoc delete (Q22)

    func testDeleteAdhocWorksOfflineAndReplays() async {
        let byHand = ItemSource(adHoc: true, mealName: nil, recipeTitle: nil, quantity: 1)
        let bags = TestData.item(name: "bin bags", sources: [byHand])
        let api = FakeShoppingAPI(list: TestData.payload([bags]))
        let store = makeStore(api)
        await store.sync()

        api.failWith = .offline
        store.deleteAdhoc(bags)
        XCTAssertFalse(store.displayItems.contains { $0.name == "bin bags" }, "gone locally at once")
        await store.sync()
        XCTAssertEqual(store.pending.count, 1)

        api.failWith = nil
        await store.sync()
        XCTAssertTrue(store.pending.isEmpty)
        XCTAssertEqual(api.deleted, [bags.id])
    }

    func testDeleteOfJustAddedAdhocFollowsServerRemap() async {
        // Offline: quick-add "milk" (synthetic id), then delete it. Online,
        // the server merges the add into an EXISTING line with a different id
        // — the queued delete must chase it there, same as a check-off would.
        let serverMilkId = UUID()
        let api = FakeShoppingAPI(list: TestData.payload([]))
        api.failWith = .offline
        let store = makeStore(api)
        store.addAdhoc(name: "milk", quantity: 500, unit: "ml")
        await store.sync()
        let synthetic = store.displayItems.first { $0.name == "milk" }!
        store.deleteAdhoc(synthetic)
        await store.sync()
        XCTAssertEqual(store.pending.count, 2)

        api.failWith = nil
        api.addItemResult = { _ in TestData.item(id: serverMilkId, name: "milk", quantity: 1500, unit: "ml") }
        await store.sync()

        XCTAssertEqual(api.deleted, [serverMilkId], "delete re-targeted to the server's merged item id")
    }

    func testRejectedDeleteIsDroppedAndTruthRestored() async {
        // A meal claimed the line while the delete sat in the queue: the
        // server 409s, the op is dropped, and the refetch brings the line back.
        let beef = TestData.item(name: "minced beef")
        let api = FakeShoppingAPI(list: TestData.payload([beef]))
        let store = makeStore(api)
        await store.sync()

        api.failWith = .offline
        store.deleteAdhoc(beef)
        await store.sync()

        api.failWith = nil
        api.deleteFailures[beef.id] = .server(status: 409, detail: "'minced beef' is needed by: Spag bol")
        await store.sync()

        XCTAssertTrue(store.pending.isEmpty, "the 409 op is dropped, not wedged")
        XCTAssertEqual(store.displayItems.map(\.name), ["minced beef"], "server truth restores the line")
    }

    // MARK: Refused, or just not now

    /// Replaying a tick fails with `error`: the tick stays queued, and goes
    /// once the server is answering again.
    private func assertTickSurvives(
        _ error: APIError, _ why: String, file: StaticString = #filePath, line: UInt = #line
    ) async {
        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let store = makeStore(api)
        await store.sync()

        api.patchFailures[onion.id] = error
        store.toggleChecked(onion)
        await store.sync()
        XCTAssertEqual(store.pending.count, 1, why, file: file, line: line)
        XCTAssertTrue(store.checkedItems.contains { $0.id == onion.id }, "and still shows as ticked", file: file, line: line)
        XCTAssertTrue(store.isServerUnavailable, file: file, line: line)
        XCTAssertFalse(store.isOffline, "the phone has signal; it's the server that isn't answering", file: file, line: line)
        XCTAssertNil(store.errorMessage, "nothing was refused, so there's nothing to report", file: file, line: line)

        api.patchFailures = [:]
        await store.sync()
        XCTAssertTrue(store.pending.isEmpty, file: file, line: line)
        XCTAssertEqual(api.patches.map(\.id), [onion.id], "sent once the server is back", file: file, line: line)
        XCTAssertFalse(store.isServerUnavailable, file: file, line: line)
    }

    func testA500KeepsTheOp() async {
        await assertTickSurvives(
            .server(status: 500, detail: "Internal Server Error"), "a 500 is the server's failing, not the op's"
        )
    }

    func testA503KeepsTheOp() async {
        await assertTickSurvives(
            .server(status: 503, detail: APIClient.unexplainedDetail(status: 503)),
            "an API restarting mid-deploy must not cost somebody their ticks"
        )
    }

    func testA429KeepsTheOp() async {
        await assertTickSurvives(
            .server(status: 429, detail: "Too many requests"), "rate limited means later, not never"
        )
    }

    func testA408KeepsTheOp() async {
        await assertTickSurvives(.server(status: 408, detail: "Request timed out"), "a timeout is worth another go")
    }

    func testAnUnreadableReplyKeepsTheOp() async {
        // A captive portal answering 200 with its sign-in page.
        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let store = makeStore(api)
        await store.sync()

        api.failWithError = DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "<html>"))
        store.toggleChecked(onion)
        await store.sync()
        XCTAssertEqual(store.pending.count, 1, "a reply that isn't the API's says nothing about the op")

        api.failWithError = nil
        await store.sync()
        XCTAssertTrue(store.pending.isEmpty)
    }

    func testOnlyTheAPIsOwnRefusalDropsAnOp() async {
        // A proxy's 403 page and the API's 403 share a number and nothing else.
        await assertTickSurvives(
            .server(status: 403, detail: APIClient.unexplainedDetail(status: 403)),
            "a 403 with no word from the API behind it is somebody else's page"
        )

        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let store = makeStore(api)
        await store.sync()
        api.patchFailures[onion.id] = .server(status: 403, detail: "that list item isn't yours")
        store.toggleChecked(onion)
        await store.sync()
        XCTAssertTrue(store.pending.isEmpty, "the API saying no, in words, is final")
    }

    func testARefusalIsShownRatherThanSwallowed() async {
        let ghost = TestData.item(name: "ghost")
        let api = FakeShoppingAPI(list: TestData.payload([ghost]))
        let store = makeStore(api)
        await store.sync()

        api.patchFailures[ghost.id] = .server(
            status: 404, detail: "list item not found; fetch current items via GET /shopping-list"
        )
        store.toggleChecked(ghost)
        await store.sync()
        XCTAssertTrue(store.pending.isEmpty)
        XCTAssertEqual(
            store.errorMessage,
            "Couldn't sync a change to ghost: list item not found; fetch current items via GET /shopping-list"
        )

        // One the app can't see coming: a word the server doesn't take as a unit.
        api.addFailure = .server(status: 422, detail: "ingredient 'milk': unit 'glug' is not accepted")
        store.addAdhoc(name: "milk", quantity: 2, unit: "glug")
        await store.sync()
        XCTAssertTrue(store.pending.isEmpty)
        XCTAssertEqual(
            store.errorMessage?.components(separatedBy: "\n").last,
            "Couldn't add milk: ingredient 'milk': unit 'glug' is not accepted",
            "every refusal is said, not just the latest"
        )
    }

    func testARevokedTokenSignsOutButKeepsTheQueue() async {
        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        var signedOut = false
        let store = makeStore(api, onUnauthorized: { signedOut = true })
        store.accountChanged(from: .signedOut, to: signedIn(alice))
        await store.sync()

        api.patchFailures[onion.id] = .unauthorized(detail: "invalid or revoked token")
        store.toggleChecked(onion)
        await store.sync()
        XCTAssertTrue(signedOut, "a 401 on replay signs the app out")
        XCTAssertEqual(store.pending.count, 1, "and costs nothing that was ticked")

        // The session signs out; the tick waits for Alice rather than going.
        store.accountChanged(from: signedIn(alice), to: .signedOut)
        api.patchFailures = [:]
        store.accountChanged(from: .signedOut, to: signedIn(alice))
        await store.sync()
        XCTAssertEqual(api.patches.map(\.id), [onion.id], "sent when she signs back in")
    }

    func testFailuresBackOffAndRetryOnTheirOwn() async {
        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let clock = RetryClock()
        let store = makeStore(api, sleep: { await clock.sleep($0) })
        await store.sync()
        XCTAssertTrue(clock.requested.isEmpty, "a sync that goes through schedules nothing")

        api.failWith = .server(status: 503, detail: "Service Unavailable")
        store.toggleChecked(onion)
        await until({ clock.requested.count == 1 && !store.isSyncing }, "a retry to be scheduled")
        XCTAssertEqual(clock.requested, [.seconds(2)])

        clock.fire()  // tries again; still down
        await until({ clock.requested.count == 2 && !store.isSyncing }, "a second retry")
        XCTAssertEqual(clock.requested.last, .seconds(4), "and backs off")
        XCTAssertEqual(store.pending.count, 1)

        api.failWith = nil
        clock.fire()  // back up
        await until({ store.pending.isEmpty && !store.isSyncing }, "the queue to drain")
        XCTAssertEqual(api.patches.map(\.id), [onion.id], "nobody had to tap anything")
        XCTAssertEqual(clock.requested.count, 2, "and once through, nothing more is scheduled")
        XCTAssertNil(store.retryTask)
    }

    func testBackoffIsCappedAtFiveMinutes() {
        let delays = (1...12).map(ShoppingListStore.retryDelay(afterFailures:))
        XCTAssertEqual(delays.prefix(4), [.seconds(2), .seconds(4), .seconds(8), .seconds(16)])
        XCTAssertEqual(delays.last, .seconds(300))
    }

    // MARK: A sync cut short, a sync asked for twice

    func testAMergedAddIsRememberedWhenTheSyncIsCutShort() async {
        // Offline: add milk, tick it. Back online just long enough for the add,
        // which the server merges into a line of its own; then the signal
        // goes before the tick.
        let serverMilkId = UUID()
        let api = FakeShoppingAPI(list: TestData.payload([]))
        let store = makeStore(api)
        await store.sync()
        api.failWith = .offline
        store.addAdhoc(name: "milk", quantity: 500, unit: "ml")
        store.toggleChecked(store.displayItems.first { $0.name == "milk" }!)
        await store.sync()
        XCTAssertEqual(store.pending.count, 2)

        api.failWith = nil
        api.addItemResult = { _ in TestData.item(id: serverMilkId, name: "milk", quantity: 1500, unit: "ml") }
        api.patchFailures[serverMilkId] = .offline
        await store.sync()

        XCTAssertEqual(store.pending.map(\.itemID), [serverMilkId], "the queued tick now names the server's line")
        XCTAssertEqual(
            store.checkedItems.map(\.id), [serverMilkId],
            "the merged line is in the cache, ticked, not gone until the next refetch"
        )

        // Relaunch: nothing held in memory survives, so it has to be on disk.
        let relaunched = makeStore(api)
        XCTAssertEqual(relaunched.pending.map(\.itemID), [serverMilkId])
        api.patchFailures = [:]
        await relaunched.sync()
        XCTAssertEqual(api.patches.map(\.id), [serverMilkId], "the tick lands on the line the milk was merged into")
    }

    func testTheResultOfEachReplayIsKeptBeforeTheNext() async {
        let onion = TestData.item(name: "onion")
        let beef = TestData.item(name: "minced beef")
        let api = FakeShoppingAPI(list: TestData.payload([onion, beef]))
        let store = makeStore(api)
        await store.sync()

        api.failWith = .offline
        store.toggleChecked(onion)
        store.toggleChecked(beef)
        await store.sync()

        // The onion goes through; the signal drops before the beef.
        api.failWith = nil
        api.patchFailures[beef.id] = .offline
        await store.sync()
        XCTAssertEqual(store.pending.count, 1)
        XCTAssertEqual(
            Set(store.checkedItems.map(\.name)), ["onion", "minced beef"],
            "the onion stays ticked on the server's say-so, not flickering back until a refetch"
        )
    }

    func testASyncAskedForMidSyncRunsAgain() async {
        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let store = makeStore(api)
        let gate = Gate()
        api.fetchGate = gate

        // A sync with nothing queued, parked in its refetch.
        let first = Task { await store.sync() }
        await until({ gate.waiting == 1 }, "the first sync to reach its refetch")

        // A tick lands while it's there. It used to be told a sync was
        // running and go home; the running one had already passed the queue.
        store.toggleChecked(onion)
        let second = Task { await store.sync() }
        gate.open()
        await first.value
        await second.value

        XCTAssertTrue(store.pending.isEmpty, "the tick went in the second pass")
        XCTAssertEqual(api.patches.map(\.id), [onion.id])
        XCTAssertFalse(store.isSyncing)
    }

    // MARK: Whose list it is

    func testAnotherAccountNeverSeesOrSendsTheQueue() async {
        let api = FakeShoppingAPI(list: TestData.payload([TestData.item(name: "onion")]))
        let store = makeStore(api)
        store.accountChanged(from: .signedOut, to: signedIn(alice))
        await store.sync()
        api.failWith = .offline
        store.addAdhoc(name: "bin bags", quantity: nil, unit: nil)
        await store.sync()

        store.accountChanged(from: signedIn(alice), to: .signedOut)
        XCTAssertNil(store.cache, "signing out clears the cached list, as the privacy policy says")
        XCTAssertTrue(store.pending.isEmpty)

        api.failWith = nil
        api.list = TestData.payload([])
        store.accountChanged(from: .signedOut, to: signedIn(bob))
        await store.sync()
        XCTAssertTrue(api.added.isEmpty, "Alice's add is never posted into Bob's list")
        XCTAssertTrue(store.displayItems.isEmpty, "and Bob never sees her list, not even from the cache")

        // Relaunched as Bob: still held back, from disk this time.
        let relaunched = makeStore(api)
        relaunched.accountChanged(from: .signedOut, to: signedIn(nil))
        relaunched.accountChanged(from: signedIn(nil), to: signedIn(bob))
        await relaunched.sync()
        XCTAssertTrue(api.added.isEmpty)

        // Alice back: her add goes, to her list.
        relaunched.accountChanged(from: signedIn(bob), to: .signedOut)
        relaunched.accountChanged(from: .signedOut, to: signedIn(alice))
        XCTAssertEqual(relaunched.pending.count, 1, "waiting for her all along")
        await relaunched.sync()
        XCTAssertEqual(api.added.map(\.name), ["bin bags"])
    }

    func testMovingHouseholdClearsTheCacheAndHoldsTheQueue() async {
        let api = FakeShoppingAPI(list: TestData.payload([TestData.item(name: "onion")]))
        let store = makeStore(api)
        store.accountChanged(from: .signedOut, to: signedIn(alice))
        await store.sync()
        api.failWith = .offline
        store.addAdhoc(name: "bin bags", quantity: nil, unit: nil)
        await store.sync()

        // Joined another household: same account, somebody else's list.
        let moved = DataOwner(server: alice.server, userId: alice.userId, householdId: UUID())
        store.accountChanged(from: signedIn(alice), to: signedIn(moved))
        XCTAssertNil(store.cache, "the old household's list goes")
        XCTAssertTrue(store.pending.isEmpty, "and its queue isn't sent into the new one")

        api.failWith = nil
        await store.sync()
        XCTAssertTrue(api.added.isEmpty)
    }

    func testLeavingAHouseholdSetsTheListAsideBeforeTheNewOneIsKnown() async {
        let api = FakeShoppingAPI(list: TestData.payload([TestData.item(name: "onion")]))
        let store = makeStore(api)
        store.accountChanged(from: .signedOut, to: signedIn(alice))
        await store.sync()
        api.failWith = .offline
        store.addAdhoc(name: "bin bags", quantity: nil, unit: nil)
        await store.sync()

        // Left: still signed in, but not in the household any of this is for,
        // and not yet told which one this account landed in.
        store.accountChanged(from: signedIn(alice), to: signedIn(nil))
        XCTAssertNil(store.cache)
        XCTAssertTrue(store.pending.isEmpty)

        let landed = DataOwner(server: alice.server, userId: alice.userId, householdId: UUID())
        store.accountChanged(from: signedIn(nil), to: signedIn(landed))
        api.failWith = nil
        await store.sync()
        XCTAssertTrue(api.added.isEmpty, "the old household's add stays with the old household")
    }

    func testDeletingTheAccountWipesItsCacheAndQueue() async {
        let api = FakeShoppingAPI(list: TestData.payload([TestData.item(name: "onion")]))
        let store = makeStore(api)
        let elsewhere = DataOwner(server: alice.server, userId: alice.userId, householdId: UUID())
        // Something held for Alice from a household she's since left.
        store.accountChanged(from: .signedOut, to: signedIn(elsewhere))
        api.failWith = .offline
        store.addAdhoc(name: "bin bags", quantity: nil, unit: nil)
        store.accountChanged(from: signedIn(elsewhere), to: signedIn(alice))
        store.addAdhoc(name: "milk", quantity: 1, unit: "l")
        api.failWith = nil
        await store.sync()
        api.failWith = .offline
        store.addAdhoc(name: "eggs", quantity: 6, unit: "item")
        await store.sync()
        XCTAssertNotNil(store.cache)
        XCTAssertEqual(store.pending.count, 1)

        store.accountDeleted(alice)
        store.accountChanged(from: signedIn(alice), to: .signedOut)
        XCTAssertNil(store.cache)
        XCTAssertTrue(store.pending.isEmpty)

        // Nothing of hers is left to come back, in either household or on disk.
        store.accountChanged(from: .signedOut, to: signedIn(alice))
        XCTAssertTrue(store.pending.isEmpty)
        store.accountChanged(from: signedIn(alice), to: signedIn(elsewhere))
        XCTAssertTrue(store.pending.isEmpty)
        let relaunched = makeStore(api)
        XCTAssertNil(relaunched.cache)
        XCTAssertTrue(relaunched.pending.isEmpty)
    }

    func testAQueueFromBeforeOwnersBelongsToTheSessionThatResumes() async throws {
        // Written by an older build: a bare array, no owner anywhere.
        let op = PendingOp.addAdhoc(id: UUID(), name: "milk", quantity: 1000, unit: "ml")
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        try JSONEncoder().encode([op]).write(to: tempDir.appending(path: "pending-ops.json"))

        let api = FakeShoppingAPI(list: TestData.payload([]))
        let store = makeStore(api)
        XCTAssertEqual(store.pending, [op], "an update doesn't lose the queue")

        // Launched signed in (the same session that queued it), then identified.
        store.accountChanged(from: .signedOut, to: signedIn(nil))
        store.accountChanged(from: signedIn(nil), to: signedIn(alice))
        XCTAssertEqual(store.owner, alice)
        XCTAssertEqual(store.pending, [op])
        await store.sync()
        XCTAssertEqual(api.added.map(\.name), ["milk"])
    }

    func testAQueueFromBeforeOwnersIsNeverSentAsWhoeverSignsInNext() async throws {
        let op = PendingOp.addAdhoc(id: UUID(), name: "milk", quantity: 1000, unit: "ml")
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        try JSONEncoder().encode([op]).write(to: tempDir.appending(path: "pending-ops.json"))

        // Launched signed out: whoever queued it has gone, and there's no
        // telling who they were.
        let api = FakeShoppingAPI(list: TestData.payload([]))
        let store = makeStore(api)
        store.accountChanged(from: .signedOut, to: .signedOut)
        store.accountChanged(from: .signedOut, to: signedIn(bob))
        await store.sync()
        XCTAssertTrue(api.added.isEmpty)
    }

    func testAReplyForTheOldAccountNeverLandsInTheNewOnesCache() async {
        let api = FakeShoppingAPI(list: TestData.payload([TestData.item(name: "alice's onion")]))
        let store = makeStore(api)
        store.accountChanged(from: .signedOut, to: signedIn(alice))
        let gate = Gate()
        api.fetchGate = gate
        let syncing = Task { await store.sync() }
        await until({ gate.waiting == 1 }, "Alice's refetch to be in flight")

        // She signs out and Bob signs in while her list is on its way back.
        store.accountChanged(from: signedIn(alice), to: .signedOut)
        store.accountChanged(from: .signedOut, to: signedIn(bob))
        gate.open()
        await syncing.value

        XCTAssertNil(store.cache, "Alice's list must not land in Bob's cache")
        XCTAssertEqual(store.owner, bob)
    }

    // MARK: Finishing the shop

    func testFinishingTheShopSendsTheQueueBeforeArchiving() async throws {
        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let store = makeStore(api)
        await store.sync()
        api.failWith = .offline
        store.toggleChecked(onion)
        store.addAdhoc(name: "bin bags", quantity: nil, unit: nil)
        await store.sync()

        api.failWith = nil
        try await store.finishShop()

        let archived = try XCTUnwrap(api.calls.firstIndex(of: "archive"))
        XCTAssertEqual(
            api.calls[..<archived].filter { $0 == "patch" || $0 == "add" }, ["patch", "add"],
            "the tick and the add land on the list they were made on, before it's archived"
        )
        XCTAssertEqual(api.calls.suffix(2), ["fetch", "aisles"], "then the fresh list is fetched")
        XCTAssertTrue(store.pending.isEmpty)
        XCTAssertTrue(store.displayItems.isEmpty && store.checkedItems.isEmpty, "and it starts empty")
    }

    func testFinishingTheShopWaitsForChangesStillSyncing() async {
        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let store = makeStore(api)
        await store.sync()

        api.patchFailures[onion.id] = .server(status: 503, detail: "Service Unavailable")
        store.toggleChecked(onion)
        do {
            try await store.finishShop()
            XCTFail("a shop with changes still queued must not be archived")
        } catch let error as StillSyncing {
            XCTAssertEqual(error.count, 1)
            XCTAssertTrue(error.localizedDescription.hasPrefix("1 change still syncing"), error.localizedDescription)
        } catch {
            XCTFail("unexpected error: \(error)")
        }
        XCTAssertEqual(api.archiveCount, 0)
        XCTAssertEqual(store.pending.count, 1, "the tick is still there to send")
    }

    func testAFinishedShopShowsTheFreshListEvenOffline() async throws {
        let onion = TestData.item(name: "onion")
        let api = FakeShoppingAPI(list: TestData.payload([onion]))
        let store = makeStore(api)
        await store.sync()

        // Archived, and the signal goes before the fresh list comes back.
        api.onArchive = { [unowned api] in api.failWith = .offline }
        try await store.finishShop()
        XCTAssertEqual(api.archiveCount, 1)
        XCTAssertTrue(store.displayItems.isEmpty, "the archived list isn't shown as if it were still this shop")
    }

    // MARK: Quick add

    func testAnAddTheServerWouldRefuseIsNeverQueued() {
        let store = makeStore(FakeShoppingAPI(list: TestData.payload([])))
        XCTAssertEqual(
            store.addAdhoc(name: "milk", quantity: 4, unit: "pints"),
            "1 UK pint = 568 ml. The list only takes metric or a count."
        )
        XCTAssertNotNil(store.addAdhoc(name: "milk", quantity: 0, unit: "l"))
        XCTAssertNotNil(store.addAdhoc(name: "milk", quantity: -1, unit: "l"))
        XCTAssertNotNil(store.addAdhoc(name: "milk", quantity: .infinity, unit: "l"))
        XCTAssertNotNil(store.addAdhoc(name: "milk", quantity: .nan, unit: "l"))
        XCTAssertNotNil(store.addAdhoc(name: String(repeating: "a", count: 201), quantity: nil, unit: nil))
        XCTAssertTrue(store.pending.isEmpty, "nothing the server would refuse is queued to be dropped later")

        XCTAssertNil(store.addAdhoc(name: "milk", quantity: 2, unit: "l"))
        XCTAssertEqual(store.pending.count, 1)
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

    /// The server stores and serves amounts past Int.max, and `Int(value)`
    /// traps on them: one such line crashed the Shopping tab on every phone
    /// in the household.
    func testDisplayQuantityNeverTraps() {
        XCTAssertEqual(ShoppingListStore.displayQuantity(1e22, "g"), "1e+19 kg")
        XCTAssertEqual(ShoppingListStore.displayQuantity(1e19, "item"), "×1e+19")
        XCTAssertEqual(ShoppingListStore.displayQuantity(9e18, "ml"), "9000000000000000 l")
        XCTAssertEqual(ShoppingListStore.displayQuantity(.infinity, "tin"), "inf tins")
        XCTAssertEqual(LooseLine(name: "rice", quantity: 1e25, unit: "g").display, "rice — 1e+22 kg")
    }

    @MainActor
    func testQuickAddCatchesWhatTheServerWouldRefuse() {
        let refused = [
            "milk 4 pints", "butter 2 sticks", "mince 1 lbs", "flour 2 cups", "oil 3 tablespoons",
            "milk 0 l", "milk -1 l", "milk inf l", "milk nan ml", "rice 1 kg,",
        ]
        for text in refused {
            let (name, quantity, unit) = ShoppingListView.parseQuickAdd(text)
            XCTAssertNotNil(ShoppingListStore.adhocRejection(name: name, quantity: quantity, unit: unit), text)
        }
        for text in ["milk 2 l", "bin bags", "6 eggs", "2 tins chopped tomatoes", "frozen peas 200g"] {
            let (name, quantity, unit) = ShoppingListView.parseQuickAdd(text)
            XCTAssertNil(ShoppingListStore.adhocRejection(name: name, quantity: quantity, unit: unit), text)
        }
    }

    @MainActor
    func testTheRefusalSaysWhatToUseInstead() {
        let (name, quantity, unit) = ShoppingListView.parseQuickAdd("milk 4 pints")
        XCTAssertEqual(
            ShoppingListStore.adhocRejection(name: name, quantity: quantity, unit: unit),
            "1 UK pint = 568 ml. The list only takes metric or a count."
        )
    }
}
