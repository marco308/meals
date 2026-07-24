import Foundation

enum APIError: LocalizedError, Equatable {
    case server(status: Int, detail: String)
    case unauthorized(detail: String)
    case offline
    case invalidURL

    var errorDescription: String? {
        switch self {
        case .server(_, let detail): detail
        case .unauthorized(let detail): detail
        case .offline: "You're offline. Changes are saved and will sync when you're back."
        case .invalidURL: "Invalid server URL."
        }
    }
}

/// Thin async client for the Meals API. The backend's errors are written to
/// be shown verbatim ({"detail": "..."}), so this surfaces them as-is.
struct APIClient: Sendable {
    var baseURL: URL
    var token: String?
    var session: URLSession = .shared

    static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    private func request(_ method: String, _ path: String, query: [URLQueryItem] = [], body: Data? = nil) -> URLRequest {
        var components = URLComponents(url: baseURL.appending(path: path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { components.queryItems = query }
        var request = URLRequest(url: components.url!)
        request.httpMethod = method
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    func send<Response: Decodable>(
        _ method: String,
        _ path: String,
        query: [URLQueryItem] = [],
        json: [String: Any?]? = nil,
        as type: Response.Type
    ) async throws -> Response {
        let data = try await raw(method, path, query: query, json: json)
        return try Self.decoder().decode(Response.self, from: data)
    }

    @discardableResult
    func raw(_ method: String, _ path: String, query: [URLQueryItem] = [], json: [String: Any?]? = nil) async throws -> Data {
        var body: Data?
        if let json {
            let cleaned = json.compactMapValues { $0 }
            body = try JSONSerialization.data(withJSONObject: cleaned)
        }
        let urlRequest = request(method, path, query: query, body: body)
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: urlRequest)
        } catch {
            throw APIError.offline
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status < 400 else {
            let detail = Self.errorDetail(from: data) ?? "Request failed (\(status))"
            throw status == 401 ? APIError.unauthorized(detail: detail) : APIError.server(status: status, detail: detail)
        }
        return data
    }

    /// FastAPI errors are {"detail": "text"} or, for validation, a list of
    /// {"msg": ...} entries. Both become a readable sentence.
    static func errorDetail(from data: Data) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        if let text = object["detail"] as? String { return text }
        if let entries = object["detail"] as? [[String: Any]] {
            let messages = entries.compactMap { $0["msg"] as? String }
            if !messages.isEmpty { return messages.joined(separator: "\n") }
        }
        return nil
    }
}

// MARK: - Endpoint helpers

extension APIClient {
    func login(email: String, password: String) async throws -> AuthResponse {
        try await send("POST", "/auth/login", json: ["email": email, "password": password], as: AuthResponse.self)
    }

    func register(email: String, password: String, displayName: String) async throws -> AuthResponse {
        try await send(
            "POST", "/auth/register",
            json: ["email": email, "password": password, "display_name": displayName],
            as: AuthResponse.self
        )
    }

    func me() async throws -> UserProfile {
        try await send("GET", "/auth/me", as: UserProfile.self)
    }

    func recipes(search: String?) async throws -> [RecipeSummary] {
        var query: [URLQueryItem] = []
        if let search, !search.isEmpty { query.append(URLQueryItem(name: "search", value: search)) }
        return try await send("GET", "/recipes", query: query, as: [RecipeSummary].self)
    }

    func recipe(id: UUID) async throws -> Recipe {
        try await send("GET", "/recipes/\(id.uuidString.lowercased())", as: Recipe.self)
    }

    func ingest(url: String) async throws -> IngestResponse {
        try await send("POST", "/recipes/ingest", json: ["url": url], as: IngestResponse.self)
    }

    func meals() async throws -> [Meal] {
        try await send("GET", "/meals", as: [Meal].self)
    }

    func createMeal(name: String, slot: String?, recipeIds: [UUID]) async throws -> Meal {
        try await send(
            "POST", "/meals",
            json: ["name": name, "slot": slot, "recipe_ids": recipeIds.map { $0.uuidString.lowercased() }],
            as: Meal.self
        )
    }

    func currentPlan() async throws -> Plan {
        try await send("GET", "/plans/current", as: Plan.self)
    }

    func createPlan(label: String) async throws -> Plan {
        try await send("POST", "/plans", json: ["label": label], as: Plan.self)
    }

    func addMeal(planId: UUID, mealId: UUID) async throws -> Plan {
        try await send(
            "POST", "/plans/\(planId.uuidString.lowercased())/meals",
            json: ["meal_id": mealId.uuidString.lowercased()],
            as: Plan.self
        )
    }

    func removeMeal(planId: UUID, planMealId: UUID) async throws -> Plan {
        try await send(
            "DELETE", "/plans/\(planId.uuidString.lowercased())/meals/\(planMealId.uuidString.lowercased())",
            as: Plan.self
        )
    }

    func markCooked(planId: UUID, planMealId: UUID) async throws -> Plan {
        try await send(
            "POST", "/plans/\(planId.uuidString.lowercased())/meals/\(planMealId.uuidString.lowercased())/cooked",
            as: Plan.self
        )
    }
}

// MARK: - Shopping API (protocol so the offline store is testable)

struct AdhocPayload: Codable, Equatable, Sendable {
    let id: UUID
    let name: String
    let quantity: Double?
    let unit: String?
}

protocol ShoppingAPI: Sendable {
    func fetchList() async throws -> ShoppingListPayload
    func fetchAisles() async throws -> [Aisle]
    func patchItem(id: UUID, checked: Bool?, excluded: Bool?) async throws -> ListItem
    func addItem(_ payload: AdhocPayload) async throws -> ListItem
    func archiveList() async throws
}

extension APIClient: ShoppingAPI {
    func fetchList() async throws -> ShoppingListPayload {
        // Always fetch everything; staples/excluded are filtered client-side
        // so one cached payload serves every toggle offline.
        try await send(
            "GET", "/shopping-list",
            query: [
                URLQueryItem(name: "include_staples", value: "true"),
                URLQueryItem(name: "include_excluded", value: "true"),
            ],
            as: ShoppingListPayload.self
        )
    }

    func fetchAisles() async throws -> [Aisle] {
        try await send("GET", "/aisles", as: [Aisle].self)
    }

    func patchItem(id: UUID, checked: Bool?, excluded: Bool?) async throws -> ListItem {
        try await send(
            "PATCH", "/shopping-list/items/\(id.uuidString.lowercased())",
            json: ["checked": checked, "excluded": excluded],
            as: ListItem.self
        )
    }

    func addItem(_ payload: AdhocPayload) async throws -> ListItem {
        try await send(
            "POST", "/shopping-list/items",
            json: [
                "id": payload.id.uuidString.lowercased(),
                "name": payload.name,
                "quantity": payload.quantity,
                "unit": payload.unit,
            ],
            as: ListItem.self
        )
    }

    func archiveList() async throws {
        try await raw("POST", "/shopping-list/archive")
    }
}
