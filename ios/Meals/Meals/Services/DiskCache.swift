import Foundation

/// A read-through cache on disk for data the app only ever *reads* offline
/// (issue #33): the plan, the meal library, the recipe library and any recipe
/// you've opened.
///
/// Deliberately not a write queue. The shopping list can replay queued writes
/// because `ListItemSource.client_key` and client-supplied ids make a replayed
/// POST a no-op (Q11); meals, plans and recipes have no such contract, so a
/// queued "create meal" replayed after a timeout would duplicate it. Reads are
/// the part that's safe to serve stale, and they're most of what you need in a
/// kitchen with no signal.
struct DiskCache<Value: Codable & Sendable>: Sendable {
    let url: URL

    init(directory: URL, filename: String) {
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        self.url = directory.appending(path: filename)
    }

    /// The app's shared cache directory — the same one the shopping list uses.
    static func defaultDirectory() -> URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "Meals")
    }

    func load() -> Value? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? APIClient.decoder().decode(Value.self, from: data)
    }

    func save(_ value: Value) {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        guard let data = try? encoder.encode(value) else { return }
        try? data.write(to: url, options: .atomic)
    }

    func clear() {
        try? FileManager.default.removeItem(at: url)
    }
}

extension APIError {
    /// Whether falling back to a cached copy is the right response — as
    /// opposed to a 4xx, which means the server answered and the answer was no.
    var isConnectivity: Bool {
        if case .offline = self { return true }
        return false
    }
}
