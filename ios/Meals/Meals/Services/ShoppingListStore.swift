import Foundation
import Observation

/// A queued local mutation. Check-offs and ad-hoc adds must work in a
/// supermarket with no signal (decision Q11): each interaction appends an op,
/// the UI renders server-truth + ops, and ops replay in order once online.
enum PendingOp: Codable, Equatable, Identifiable {
    case setChecked(id: UUID, itemID: UUID, value: Bool)
    case setExcluded(id: UUID, itemID: UUID, value: Bool)
    case addAdhoc(id: UUID, name: String, quantity: Double?, unit: String?)

    var id: UUID {
        switch self {
        case .setChecked(let id, _, _), .setExcluded(let id, _, _), .addAdhoc(let id, _, _, _): id
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
    var includeChecked = true

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

    /// Items as the user should see them right now, offline or not.
    var displayItems: [ListItem] {
        var items = cache?.payload.items ?? []
        for op in pending {
            apply(op, to: &items)
        }
        items = items.filter { item in
            if item.isStaple && !includeStaples { return false }
            if item.excluded { return false }
            if item.checked && !includeChecked { return false }
            return true
        }
        let order = aisleOrder
        return items.sorted {
            let left = order[$0.aisle] ?? order.count
            let right = order[$1.aisle] ?? order.count
            return left == right ? $0.name < $1.name : left < right
        }
    }

    var hiddenStaplesCount: Int {
        var items = cache?.payload.items ?? []
        for op in pending { apply(op, to: &items) }
        return items.filter { $0.isStaple && !$0.excluded }.count
    }

    var sections: [(aisle: String, label: String, items: [ListItem])] {
        var result: [(String, String, [ListItem])] = []
        for item in displayItems {
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
        case "item": return "×\(trim(quantity))"
        default: return "\(trim(quantity)) \(unit)\(quantity == 1 ? "" : "s")"
        }
    }

    // MARK: - User actions (instant, offline-safe)

    func toggleChecked(_ item: ListItem) {
        enqueue(.setChecked(id: UUID(), itemID: item.id, value: !item.checked))
    }

    func markAlreadyHave(_ item: ListItem) {
        enqueue(.setExcluded(id: UUID(), itemID: item.id, value: true))
    }

    func addAdhoc(name: String, quantity: Double?, unit: String?) {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        enqueue(.addAdhoc(id: UUID(), name: trimmed, quantity: quantity, unit: unit))
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
            _ = try await client.patchItem(id: remap[itemID] ?? itemID, checked: value, excluded: nil)
        case .setExcluded(_, let itemID, let value):
            _ = try await client.patchItem(id: remap[itemID] ?? itemID, checked: nil, excluded: value)
        case .addAdhoc(let id, let name, let quantity, let unit):
            let item = try await client.addItem(AdhocPayload(id: id, name: name, quantity: quantity, unit: unit))
            // The server may merge into an existing line with a different id;
            // later queued ops on our synthetic id must follow it there.
            if item.id != id { remap[id] = item.id }
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
