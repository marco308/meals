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
}

struct ShoppingCache: Codable, Equatable {
    var payload: ShoppingListPayload
    var aisles: [Aisle]
}

@MainActor
@Observable
final class ShoppingListStore {
    private(set) var cache: ShoppingCache?
    private(set) var pending: [PendingOp] = []
    private(set) var isOffline = false
    private(set) var isSyncing = false
    var errorMessage: String?

    var includeStaples = false
    var includeExcluded = false

    private let api: () -> any ShoppingAPI
    private let directory: URL

    init(api: @escaping () -> any ShoppingAPI, directory: URL? = nil) {
        self.api = api
        let base = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "Meals")
        self.directory = base
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
        func trim(_ value: Double) -> String {
            value == value.rounded() ? String(Int(value)) : String(value)
        }
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
        enqueue(.setChecked(id: UUID(), itemID: item.id, value: !item.checked))
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

    func addAdhoc(name: String, quantity: Double?, unit: String?) {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        enqueue(.addAdhoc(id: UUID(), name: trimmed, quantity: quantity, unit: unit))
    }

    /// Delete a line added by hand (Q22) — a typo'd quick-add, mostly. Only
    /// offered when every source is ad-hoc: meal-sourced lines come off by
    /// taking the meal off the plan, and the server would 409 anyway.
    func deleteAdhoc(_ item: ListItem) {
        enqueue(.deleteItem(id: UUID(), itemID: item.id))
    }

    private func enqueue(_ op: PendingOp) {
        pending.append(op)
        persistPending()
        Task { await sync() }
    }

    // MARK: - Sync

    /// Replay queued ops in order, then refetch server truth. Any network
    /// failure stops quietly: ops stay queued, cache keeps serving the UI.
    func sync() async {
        guard !isSyncing else { return }
        isSyncing = true
        defer { isSyncing = false }
        let client = api()

        var remap: [UUID: UUID] = [:]
        while let op = pending.first {
            do {
                try await replay(op, client: client, remap: &remap)
                pending.removeFirst()
                persistPending()
            } catch let error as APIError where error == .offline {
                isOffline = true
                return
            } catch APIError.unauthorized {
                return
            } catch {
                // Server rejected the op (e.g. item deleted) — drop it rather
                // than wedge the queue; the refetch below restores truth.
                pending.removeFirst()
                persistPending()
            }
        }

        do {
            let payload = try await client.fetchList()
            let aisles = (try? await client.fetchAisles()) ?? cache?.aisles ?? []
            cache = ShoppingCache(payload: payload, aisles: aisles)
            persistCache()
            isOffline = false
        } catch let error as APIError where error == .offline {
            isOffline = true
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func replay(_ op: PendingOp, client: any ShoppingAPI, remap: inout [UUID: UUID]) async throws {
        switch op {
        case .setChecked(_, let itemID, let value):
            _ = try await client.patchItem(id: remap[itemID] ?? itemID, checked: value, excluded: nil, stapleNeeded: nil)
        case .setExcluded(_, let itemID, let value):
            _ = try await client.patchItem(id: remap[itemID] ?? itemID, checked: nil, excluded: value, stapleNeeded: nil)
        case .setStapleNeeded(_, let itemID, let value):
            _ = try await client.patchItem(id: remap[itemID] ?? itemID, checked: nil, excluded: nil, stapleNeeded: value)
        case .addAdhoc(let id, let name, let quantity, let unit):
            let item = try await client.addItem(AdhocPayload(id: id, name: name, quantity: quantity, unit: unit))
            // The server may merge into an existing line with a different id;
            // later queued ops on our synthetic id must follow it there.
            if item.id != id { remap[id] = item.id }
        case .deleteItem(_, let itemID):
            try await client.deleteItem(id: remap[itemID] ?? itemID)
        }
    }

    func finishShop() async throws {
        try await api().archiveList()
        await sync()
    }

    // MARK: - Persistence

    private func load() {
        if let data = try? Data(contentsOf: cacheURL) {
            cache = try? APIClient.decoder().decode(ShoppingCache.self, from: data)
        }
        if let data = try? Data(contentsOf: pendingURL),
           let ops = try? JSONDecoder().decode([PendingOp].self, from: data) {
            pending = ops
        }
    }

    private func persistCache() {
        guard let cache else { return }
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        if let data = try? encoder.encode(cache) {
            try? data.write(to: cacheURL, options: .atomic)
        }
    }

    private func persistPending() {
        if let data = try? JSONEncoder().encode(pending) {
            try? data.write(to: pendingURL, options: .atomic)
        }
    }
}
