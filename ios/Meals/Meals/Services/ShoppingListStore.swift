import Foundation
import Observation

/// A queued local mutation. Check-offs and ad-hoc adds must work in a
/// supermarket with no signal (decision Q11): each interaction appends an op,
/// the UI renders server-truth + ops, and ops replay in order once online.
enum PendingOp: Codable, Equatable, Identifiable {
    case setChecked(id: UUID, itemID: UUID, value: Bool)
    case setExcluded(id: UUID, itemID: UUID, value: Bool)
    case setStapleNeeded(id: UUID, itemID: UUID, value: Bool)
    case addAdhoc(id: UUID, name: String, quantity: Double?, unit: String?)
    case deleteItem(id: UUID, itemID: UUID)

    var id: UUID {
        switch self {
        case .setChecked(let id, _, _), .setExcluded(let id, _, _), .setStapleNeeded(let id, _, _),
             .addAdhoc(let id, _, _, _), .deleteItem(let id, _): id
        }
    }

    /// The line this op changes. nil for an add, which makes one: its own id
    /// stands in for that line until the server says which it is.
    var itemID: UUID? {
        switch self {
        case .setChecked(_, let itemID, _), .setExcluded(_, let itemID, _), .setStapleNeeded(_, let itemID, _),
             .deleteItem(_, let itemID): itemID
        case .addAdhoc: nil
        }
    }

    /// The same op, aimed at `new` if it was aimed at `old`.
    func retargeted(from old: UUID, to new: UUID) -> PendingOp {
        switch self {
        case .setChecked(let id, old, let value): .setChecked(id: id, itemID: new, value: value)
        case .setExcluded(let id, old, let value): .setExcluded(id: id, itemID: new, value: value)
        case .setStapleNeeded(let id, old, let value): .setStapleNeeded(id: id, itemID: new, value: value)
        case .deleteItem(let id, old): .deleteItem(id: id, itemID: new)
        default: self
        }
    }
}

struct ShoppingCache: Codable, Equatable {
    var payload: ShoppingListPayload
    var aisles: [Aisle]
    /// Whose list this is. nil in a cache written before owners were recorded.
    var owner: DataOwner? = nil
}

/// Ops queued by somebody other than whoever is signed in now, set aside until
/// they are back (see `ShoppingListStore.accountChanged`).
struct HeldQueue: Codable, Equatable {
    let owner: DataOwner
    var ops: [PendingOp]
}

/// A shop can't be finished while changes are still queued: ticks would land
/// on the archived list and adds on the fresh one.
struct StillSyncing: LocalizedError, Equatable {
    let count: Int

    var errorDescription: String? {
        count == 1
            ? "1 change still syncing. Finish the shop once it has reached the server, or it will end up on the wrong list."
            : "\(count) changes still syncing. Finish the shop once they have reached the server, or they will end up on the wrong list."
    }
}

@MainActor
@Observable
final class ShoppingListStore {
    private(set) var cache: ShoppingCache?
    private(set) var pending: [PendingOp] = []
    /// Whose `cache` and `pending` are. nil until an owner is known: a queue
    /// from before owners were recorded, or nothing on disk at all.
    private(set) var owner: DataOwner?
    private(set) var isOffline = false
    /// The phone has signal, but the server isn't answering properly: a 5xx
    /// while it restarts, a 429, a page that isn't the API's. The queue waits
    /// exactly as it does offline; only the words differ.
    private(set) var isServerUnavailable = false
    private(set) var isSyncing = false
    /// What the server turned down, in its own words, for the list to show.
    var errorMessage: String?

    var includeStaples = false
    var includeExcluded = false

    /// Lines ticked off on this phone, newest last: what "Undo" walks back
    /// through when a thumb lands on the wrong row in the supermarket. Kept in
    /// memory only; it is about the last few seconds, not the whole shop.
    private(set) var tickHistory: [UUID] = []

    @ObservationIgnored private var held: [HeldQueue] = []
    /// Only a signed-in store talks to the server. True until told otherwise,
    /// so a store with no session behind it (tests) behaves as it always has.
    @ObservationIgnored private var isSignedIn = true
    /// Bumped whenever the cache and queue change hands. A sync notes it
    /// before every await and drops whatever it was doing if it moved: a reply
    /// fetched for one owner must never land in another's cache.
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var syncTask: Task<Void, Never>?
    @ObservationIgnored private var syncAgain = false
    @ObservationIgnored private(set) var retryTask: Task<Void, Never>?
    @ObservationIgnored private var failureStreak = 0

    private let api: () -> any ShoppingAPI
    private let directory: URL
    private let onUnauthorized: () -> Void
    private let sleep: @Sendable (Duration) async throws -> Void

    /// `onUnauthorized` is how a token the server no longer accepts signs the
    /// app out; the queue survives it. `sleep` is how the store waits before
    /// retrying, injectable so tests can hold or release a retry.
    init(
        api: @escaping () -> any ShoppingAPI,
        directory: URL? = nil,
        onUnauthorized: @escaping () -> Void = {},
        sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }
    ) {
        self.api = api
        let base = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "Meals")
        self.directory = base
        self.onUnauthorized = onUnauthorized
        self.sleep = sleep
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        load()
    }

    private var cacheURL: URL { directory.appending(path: "shopping-cache.json") }
    private var pendingURL: URL { directory.appending(path: "pending-ops.json") }

    // MARK: - Projection (server truth + pending ops, filtered for display)

    /// Server truth with pending ops applied — the base every view filters.
    private var projectedItems: [ListItem] {
        var items = cache?.payload.items ?? []
        for op in pending {
            apply(op, to: &items)
        }
        return items
    }

    /// Still to buy: what the user should see right now, offline or not. A
    /// staple marked "I'm low" in the staples check stays visible in its aisle.
    /// Checking something off takes it out of here — the basket is what's left,
    /// so the aisle you're standing in only ever shows what you still need.
    var displayItems: [ListItem] {
        aisleSorted(visibleItems.filter { !$0.checked })
    }

    /// Already in the basket. Same visibility rules as `displayItems`, so the
    /// count under the list is exactly what dropped out of it.
    var checkedItems: [ListItem] {
        aisleSorted(visibleItems.filter(\.checked))
    }

    /// Everything on this shop, checked or not, minus what's deliberately
    /// hidden (unneeded staples, "already have it" lines).
    private var visibleItems: [ListItem] {
        projectedItems.filter { item in
            if item.isStaple && !includeStaples && !item.isNeededStaple { return false }
            if item.excluded && !includeExcluded { return false }
            return true
        }
    }

    /// The pre-shop staples check: every staple on the list (minus "already
    /// have it" exclusions), walked in the same aisle order as the shop.
    var stapleCheckItems: [ListItem] {
        aisleSorted(projectedItems.filter { $0.isStaple && !$0.excluded })
    }

    var sections: [(aisle: String, label: String, items: [ListItem])] { grouped(displayItems) }

    var stapleCheckSections: [(aisle: String, label: String, items: [ListItem])] { grouped(stapleCheckItems) }

    private func aisleSorted(_ items: [ListItem]) -> [ListItem] {
        let order = aisleOrder
        return items.sorted {
            let left = order[$0.aisle] ?? order.count
            let right = order[$1.aisle] ?? order.count
            return left == right ? $0.name < $1.name : left < right
        }
    }

    private func grouped(_ items: [ListItem]) -> [(aisle: String, label: String, items: [ListItem])] {
        var result: [(String, String, [ListItem])] = []
        for item in items {
            if result.last?.0 == item.aisle {
                result[result.count - 1].2.append(item)
            } else {
                result.append((item.aisle, item.aisleLabel, [item]))
            }
        }
        return result
    }

    private var aisleOrder: [String: Int] {
        let emojis = cache?.aisles.isEmpty == false ? cache!.aisles.map(\.emoji) : AisleOrder.fallback
        return Dictionary(uniqueKeysWithValues: emojis.enumerated().map { ($1, $0) })
    }

    private func apply(_ op: PendingOp, to items: inout [ListItem]) {
        switch op {
        case .setChecked(_, let itemID, let value):
            if let index = items.firstIndex(where: { $0.id == itemID }) {
                items[index].checked = value
            }
        case .setExcluded(_, let itemID, let value):
            if let index = items.firstIndex(where: { $0.id == itemID }) {
                items[index].excluded = value
            }
        case .setStapleNeeded(_, let itemID, let value):
            if let index = items.firstIndex(where: { $0.id == itemID }) {
                items[index].stapleNeeded = value
            }
        case .deleteItem(_, let itemID):
            items.removeAll { $0.id == itemID }
        case .addAdhoc(let id, let name, let quantity, let unit):
            let canonical = name.lowercased().trimmingCharacters(in: .whitespaces)
            if let index = items.firstIndex(where: { $0.name == canonical && $0.unit == unit }) {
                if let quantity {
                    items[index].quantity = (items[index].quantity ?? 0) + quantity
                    items[index].display = Self.displayQuantity(items[index].quantity, unit)
                }
                items[index].checked = false
                items[index].sources.append(ItemSource(adHoc: true, mealName: nil, recipeTitle: nil, quantity: quantity))
            } else {
                items.append(
                    ListItem(
                        id: id,
                        ingredientId: id,
                        name: canonical,
                        aisle: "❓",
                        aisleLabel: "Unknown",
                        isStaple: false,
                        quantity: quantity,
                        unit: unit,
                        display: Self.displayQuantity(quantity, unit),
                        checked: false,
                        excluded: false,
                        sources: [ItemSource(adHoc: true, mealName: nil, recipeTitle: nil, quantity: quantity)]
                    )
                )
            }
        }
    }

    nonisolated static func displayQuantity(_ quantity: Double?, _ unit: String?) -> String {
        guard let quantity, let unit else { return "" }
        let trim = MealsUnits.amountText
        switch unit {
        case "g" where quantity >= 1000: return "\(trim(quantity / 1000)) kg"
        case "ml" where quantity >= 1000: return "\(trim(quantity / 1000)) l"
        case "g", "ml": return "\(trim(quantity)) \(unit)"
        case "item", "items": return "×\(trim(quantity))"
        // don't double-pluralise units the user already typed as plural
        default: return "\(trim(quantity)) \(unit)\(quantity == 1 || unit.hasSuffix("s") ? "" : "s")"
        }
    }

    // MARK: - User actions (instant, offline-safe)

    func toggleChecked(_ item: ListItem) {
        let value = !item.checked
        tickHistory.removeAll { $0 == item.id }
        if value {
            tickHistory.append(item.id)
            if tickHistory.count > 50 { tickHistory.removeFirst() }
        }
        enqueue(.setChecked(id: UUID(), itemID: item.id, value: value))
    }

    /// The line "Undo" would un-tick: the latest tick made here that is still
    /// ticked and still on the list. Anything the household has since
    /// un-ticked, deleted or hidden is skipped rather than resurrected.
    var lastTicked: ListItem? {
        let visible = Dictionary(visibleItems.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        for id in tickHistory.reversed() {
            if let item = visible[id], item.checked { return item }
        }
        return nil
    }

    /// Put the last line ticked off back in its aisle. Just another queued
    /// un-tick, so it works offline like any other.
    func undoLastTick() {
        guard let item = lastTicked else { return }
        if let index = tickHistory.lastIndex(of: item.id) {
            // Anything newer was skipped as stale; it goes too.
            tickHistory.removeSubrange(index...)
        }
        enqueue(.setChecked(id: UUID(), itemID: item.id, value: false))
    }

    func markAlreadyHave(_ item: ListItem) {
        enqueue(.setExcluded(id: UUID(), itemID: item.id, value: true))
    }

    /// Undo "already have it" — the item returns to this shop.
    func putBack(_ item: ListItem) {
        enqueue(.setExcluded(id: UUID(), itemID: item.id, value: false))
    }

    /// Staples check: "I'm low" — put this staple on the main list.
    func markStapleNeeded(_ item: ListItem) {
        enqueue(.setStapleNeeded(id: UUID(), itemID: item.id, value: true))
    }

    /// "Have it after all" — the staple goes back to hidden.
    func unmarkStapleNeeded(_ item: ListItem) {
        enqueue(.setStapleNeeded(id: UUID(), itemID: item.id, value: false))
    }

    /// Excluded items with pending ops applied — for the "already have" view.
    var excludedCount: Int {
        projectedItems.filter(\.excluded).count
    }

    /// Queue an ad-hoc add, unless the server would refuse it. Then nothing is
    /// queued and the reason comes back for the caller to show beside what was
    /// typed: a refused op is dropped on replay, which would lose it silently.
    @discardableResult
    func addAdhoc(name: String, quantity: Double?, unit: String?) -> String? {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let unit = unit?.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanUnit = unit?.isEmpty == false ? unit : nil
        if let problem = Self.adhocRejection(name: trimmed, quantity: quantity, unit: cleanUnit) {
            return problem
        }
        enqueue(.addAdhoc(id: UUID(), name: trimmed, quantity: quantity, unit: cleanUnit))
        return nil
    }

    /// Why `POST /shopping-list/items` would turn this down, or nil if it
    /// wouldn't: the checks in the backend's `IngredientLineIn` that a quick
    /// add can trip. Anything this misses is still caught on replay and shown.
    nonisolated static func adhocRejection(name: String, quantity: Double?, unit: String?) -> String? {
        if name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "Type something to add."
        }
        // Counted the way the server counts, in code points, not characters.
        if name.unicodeScalars.count > 200 {
            return "That's too long for one line of a shopping list. Keep it under 200 characters."
        }
        if let problem = MealsUnits.rejection(forAmount: quantity) {
            return "\(problem)."
        }
        if let problem = MealsUnits.rejection(for: unit) {
            return "\(problem). The list only takes metric or a count."
        }
        if (quantity == nil) != (unit == nil) {
            return "An amount needs a unit and a unit needs an amount, like 2 tins or 500 g."
        }
        return nil
    }

    /// Delete a line added by hand (Q22) — a typo'd quick-add, mostly. Only
    /// offered when every source is ad-hoc: meal-sourced lines come off by
    /// taking the meal off the plan, and the server would 409 anyway.
    func deleteAdhoc(_ item: ListItem) {
        enqueue(.deleteItem(id: UUID(), itemID: item.id))
    }

    private func enqueue(_ op: PendingOp) {
        pending.append(op)
        persistQueue()
        Task { await sync() }
    }

    // MARK: - Whose list this is

    /// Who is signed in changed (`Session.onAccountChange`). The cache and the
    /// queue belong to exactly one owner (server, account, household) and
    /// follow the session here: kept while it is only resuming, adopted by the
    /// first owner it learns, and set aside for anyone else. Nothing one
    /// account cached is shown to another, and nothing one queued is ever
    /// sent as another.
    func accountChanged(from old: AccountState, to new: AccountState) {
        guard new.signedIn else {
            isSignedIn = false
            setAside(for: owner ?? old.owner)
            return
        }
        isSignedIn = true
        guard let newOwner = new.owner else {
            // Signed in but not identified. Straight after being identified,
            // that means the household this list belongs to is no longer ours
            // (we left it) and the server hasn't said which one we're in now.
            // At launch it's just the same session resuming, and it keeps
            // what's here.
            if old.signedIn, let previous = old.owner {
                setAside(for: owner ?? previous)
            }
            return
        }
        guard owner != newOwner else { return }
        if owner != nil {
            // Somebody else's, or another household's: held back, not thrown away.
            setAside(for: owner)
        }
        adopt(newOwner)
    }

    /// The account was deleted: everything it left on this phone goes with it,
    /// including anything held for it from another household.
    func accountDeleted(_ deleted: DataOwner?) {
        if let gone = deleted ?? owner {
            held.removeAll { $0.owner.server == gone.server && $0.owner.userId == gone.userId }
        }
        pending = []
        cache = nil
        owner = nil
        resetSync()
        persistQueue()
        persistCache()
    }

    /// Put the cache and the queue out of reach. The queue is held for
    /// `holder`, and goes back on the moment they're signed in again; the
    /// cache is thrown away (the privacy policy promises as much, and the
    /// server will send it again).
    private func setAside(for holder: DataOwner?) {
        if !pending.isEmpty, let holder {
            if let index = held.firstIndex(where: { $0.owner == holder }) {
                held[index].ops += pending
            } else {
                held.append(HeldQueue(owner: holder, ops: pending))
            }
        }
        // With no holder, the ops were queued before owners were recorded and
        // nobody has claimed them since. There's no account they can safely
        // be sent as, so they go rather than be sent as the wrong one.
        pending = []
        cache = nil
        owner = nil
        resetSync()
        persistQueue()
        persistCache()
    }

    /// This owner's from now on: whatever is here (a session resuming, or
    /// nothing at all), plus anything held for them, oldest first.
    private func adopt(_ newOwner: DataOwner) {
        owner = newOwner
        cache?.owner = newOwner
        if let index = held.firstIndex(where: { $0.owner == newOwner }) {
            pending = held.remove(at: index).ops + pending
            // Their older ops now go first; an in-flight replay mustn't
            // settle out of that order.
            generation += 1
            Task { await sync() }
        }
        persistQueue()
        persistCache()
    }

    /// Nothing in flight may land after the cache and queue change hands.
    private func resetSync() {
        tickHistory = []
        generation += 1
        retryTask?.cancel()
        retryTask = nil
        failureStreak = 0
        isOffline = false
        isServerUnavailable = false
        errorMessage = nil
    }

    // MARK: - Sync

    /// Replay queued ops in order, then refetch server truth. A failure worth
    /// retrying stops quietly: ops stay queued, the cache keeps serving the
    /// UI, and another try is scheduled. Called while a sync is already
    /// running, it has that one go round again and waits for it, so whatever
    /// the caller just queued has been tried by the time this returns.
    func sync() async {
        guard isSignedIn else { return }
        if let running = syncTask {
            syncAgain = true
            await running.value
            return
        }
        let task = Task { await syncUntilSettled() }
        syncTask = task
        await task.value
    }

    private func syncUntilSettled() async {
        isSyncing = true
        var outcome: SyncOutcome
        repeat {
            syncAgain = false
            outcome = await syncOnce()
        } while syncAgain && isSignedIn
        isSyncing = false
        syncTask = nil
        switch outcome {
        case .settled:
            failureStreak = 0
            retryTask?.cancel()
            retryTask = nil
        case .held:
            scheduleRetry()
        case .stopped:
            break
        }
    }

    private enum SyncOutcome {
        /// Queue drained, list refetched.
        case settled
        /// Stopped on something worth trying again: no signal, or a server
        /// that isn't answering properly.
        case held
        /// Signed out, or the list changed hands underneath: nothing to retry.
        case stopped
    }

    private func syncOnce() async -> SyncOutcome {
        guard isSignedIn else { return .stopped }
        let generation = self.generation
        let client = api()

        while let op = pending.first {
            do {
                let replayed = try await replay(op, client: client)
                guard generation == self.generation else { return .stopped }
                settle(op, as: replayed)
            } catch {
                guard generation == self.generation else { return .stopped }
                switch Self.verdict(for: error) {
                case .refused(let detail):
                    // The server read it and said no; it will say no again.
                    // Drop it rather than wedge the queue, and say why: the
                    // refetch below restores truth.
                    report(refusalMessage(for: op, detail: detail))
                    pending.removeAll { $0.id == op.id }
                    persistQueue()
                case .retryLater(let offline):
                    noteUnreachable(offline: offline)
                    return .held
                case .signedOut:
                    // The op stays queued for when this account is back.
                    onUnauthorized()
                    return .stopped
                }
            }
        }

        do {
            let payload = try await client.fetchList()
            guard generation == self.generation else { return .stopped }
            let aisles = (try? await client.fetchAisles()) ?? cache?.aisles ?? []
            guard generation == self.generation else { return .stopped }
            cache = ShoppingCache(payload: payload, aisles: aisles, owner: owner)
            persistCache()
            isOffline = false
            isServerUnavailable = false
            return .settled
        } catch {
            guard generation == self.generation else { return .stopped }
            switch Self.verdict(for: error) {
            case .refused(let detail):
                report(detail)
                return .settled
            case .retryLater(let offline):
                noteUnreachable(offline: offline)
                return .held
            case .signedOut:
                onUnauthorized()
                return .stopped
            }
        }
    }

    /// What a failed request means for the op that made it.
    enum Verdict: Equatable {
        /// The API read the request and turned it down, in words.
        case refused(String)
        /// Worth sending again later: no signal (`offline`), or a server that
        /// isn't answering properly: restarting, busy, or not the API at all
        /// (a captive portal's page, a proxy's error).
        case retryLater(offline: Bool)
        /// The token no longer works.
        case signedOut
    }

    /// Only a refusal the API itself wrote drops an op (`APIError.refusal`).
    /// Everything else keeps it, the unexpected included: an op kept too long
    /// is retried, an op dropped too soon is gone.
    nonisolated static func verdict(for error: any Error) -> Verdict {
        guard let error = error as? APIError else {
            // Chiefly a decode failure: a 200 that wasn't the API's reply.
            return .retryLater(offline: false)
        }
        if let detail = error.refusal { return .refused(detail) }
        switch error {
        case .unauthorized: return .signedOut
        case .offline: return .retryLater(offline: true)
        default: return .retryLater(offline: false)
        }
    }

    private func noteUnreachable(offline: Bool) {
        isOffline = offline
        isServerUnavailable = !offline
    }

    private func report(_ message: String) {
        errorMessage = [errorMessage, message].compactMap { $0 }.joined(separator: "\n")
    }

    private func refusalMessage(for op: PendingOp, detail: String) -> String {
        if case .addAdhoc(_, let name, _, _) = op {
            return "Couldn't add \(name): \(detail)"
        }
        let name = op.itemID.flatMap { id in cache?.payload.items.first { $0.id == id }?.name } ?? "an item"
        return "Couldn't sync a change to \(name): \(detail)"
    }

    /// Try again later, backing off: 2s, 4s, 8s … five minutes at most. A tap,
    /// the app coming to the foreground or the network coming back all sync
    /// sooner.
    private func scheduleRetry() {
        failureStreak += 1
        let delay = Self.retryDelay(afterFailures: failureStreak)
        retryTask?.cancel()
        retryTask = Task { [weak self, sleep] in
            do {
                try await sleep(delay)
            } catch {
                return
            }
            await self?.sync()
        }
    }

    nonisolated static func retryDelay(afterFailures failures: Int) -> Duration {
        let doublings = min(max(failures - 1, 0), 8)
        return .seconds(min(2 << doublings, 300))
    }

    private enum Replayed {
        case updated(ListItem)
        case added(ListItem)
        case deleted
    }

    private func replay(_ op: PendingOp, client: any ShoppingAPI) async throws -> Replayed {
        switch op {
        case .setChecked(_, let itemID, let value):
            return .updated(try await client.patchItem(id: itemID, checked: value, excluded: nil, stapleNeeded: nil))
        case .setExcluded(_, let itemID, let value):
            return .updated(try await client.patchItem(id: itemID, checked: nil, excluded: value, stapleNeeded: nil))
        case .setStapleNeeded(_, let itemID, let value):
            return .updated(try await client.patchItem(id: itemID, checked: nil, excluded: nil, stapleNeeded: value))
        case .addAdhoc(let id, let name, let quantity, let unit):
            return .added(try await client.addItem(AdhocPayload(id: id, name: name, quantity: quantity, unit: unit)))
        case .deleteItem(_, let itemID):
            try await client.deleteItem(id: itemID)
            return .deleted
        }
    }

    /// Make a replayed op part of server truth: off the queue, and into the
    /// cache the way the server answered. Both are written before anything
    /// else can go wrong, so a sync cut short here (signal gone, app killed)
    /// resumes from exactly this point, with every id where the server put it.
    private func settle(_ op: PendingOp, as replayed: Replayed) {
        pending.removeAll { $0.id == op.id }
        switch replayed {
        case .updated(let item):
            if let index = cache?.payload.items.firstIndex(where: { $0.id == item.id }) {
                cache?.payload.items[index] = item
            }
        case .added(let item):
            if item.id != op.id {
                // Merged into a line the server already had: later ops aimed
                // at our stand-in id follow it there.
                pending = pending.map { $0.retargeted(from: op.id, to: item.id) }
                tickHistory = tickHistory.map { $0 == op.id ? item.id : $0 }
            }
            if let index = cache?.payload.items.firstIndex(where: { $0.id == item.id }) {
                cache?.payload.items[index] = item
            } else {
                cache?.payload.items.append(item)
            }
        case .deleted:
            cache?.payload.items.removeAll { $0.id == op.itemID }
        }
        persistQueue()
        persistCache()
    }

    /// Sync first, so every tick and add lands on the list it was made on; only
    /// then archive, and start the next list from nothing.
    func finishShop() async throws {
        await sync()
        guard pending.isEmpty else { throw StillSyncing(count: pending.count) }
        let generation = self.generation
        try await api().archiveList()
        guard generation == self.generation else { return }
        // The server starts the next list empty. Show that now rather than the
        // archived one, even if the refetch below can't get through.
        if cache != nil {
            cache?.payload.items = []
            persistCache()
        }
        tickHistory = []
        await sync()
    }

    // MARK: - Persistence

    /// `pending-ops.json`: the live queue, whose it is, and what is held for
    /// others. One file, so moving ops between owners is one atomic write.
    private struct QueueFile: Codable {
        var owner: DataOwner?
        var ops: [PendingOp]
        var held: [HeldQueue]?
    }

    private func load() {
        var queueHadOwner = false
        if let data = try? Data(contentsOf: pendingURL) {
            if let file = try? JSONDecoder().decode(QueueFile.self, from: data) {
                owner = file.owner
                pending = file.ops
                held = file.held ?? []
                queueHadOwner = file.owner != nil
            } else if let ops = try? JSONDecoder().decode([PendingOp].self, from: data) {
                pending = ops  // written before owners were recorded
            }
        }
        if let data = try? Data(contentsOf: cacheURL),
           let loaded = try? APIClient.decoder().decode(ShoppingCache.self, from: data) {
            if !queueHadOwner {
                owner = loaded.owner
                cache = loaded
            } else if loaded.owner == owner {
                cache = loaded
            }
            // Otherwise it's left over from a switch the app didn't live to
            // finish; the queue's owner is the word that counts.
        }
    }

    private func persistCache() {
        guard let cache else {
            try? FileManager.default.removeItem(at: cacheURL)
            return
        }
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        if let data = try? encoder.encode(cache) {
            try? data.write(to: cacheURL, options: .atomic)
        }
    }

    private func persistQueue() {
        let file = QueueFile(owner: owner, ops: pending, held: held)
        if let data = try? JSONEncoder().encode(file) {
            try? data.write(to: pendingURL, options: .atomic)
        }
    }
}
