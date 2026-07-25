import Foundation

enum APIError: LocalizedError, Equatable {
    case server(status: Int, detail: String)
    case unauthorized(detail: String)
    case upgradeRequired(detail: String)
    case offline
    case invalidURL

    var errorDescription: String? {
        switch self {
        case .server(_, let detail): detail
        case .unauthorized(let detail): detail
        case .upgradeRequired(let detail): detail
        // Deliberately says nothing about saving or syncing: only the shopping
        // list has a queue that can keep that promise (Q11), and this error is
        // raised on every path (#33).
        case .offline: "You're offline."
        case .invalidURL: "Invalid server URL."
        }
    }
}

/// How this build introduces itself to the API: `X-Meals-Client: ios/0.1 (2)`.
/// The server compares the build number against the oldest one it still speaks
/// to and answers 426 below that — see backend `app/client_gate.py`.
enum ClientIdentity {
    static let headerName = "X-Meals-Client"

    static func headerValue(version: String, build: String) -> String {
        "ios/\(version) (\(build))"
    }

    static let current = headerValue(version: shortVersion, build: buildString)

    static var buildNumber: Int { Int(buildString) ?? 0 }

    private static var shortVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0"
    }

    private static var buildString: String {
        Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "0"
    }
}

extension Notification.Name {
    /// Posted when the server refuses this build (426). Session listens, so a
    /// mid-session deploy that raises the floor is caught wherever it lands,
    /// not just at launch.
    static let mealsUpgradeRequired = Notification.Name("MealsUpgradeRequired")
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
        request.setValue(ClientIdentity.current, forHTTPHeaderField: ClientIdentity.headerName)
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
            switch status {
            case 401:
                throw APIError.unauthorized(detail: detail)
            case 426:
                NotificationCenter.default.post(
                    name: .mealsUpgradeRequired,
                    object: nil,
                    userInfo: ["detail": detail, "upgradeUrl": Self.upgradeURL(from: data) as Any]
                )
                throw APIError.upgradeRequired(detail: detail)
            default:
                throw APIError.server(status: status, detail: detail)
            }
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

    /// The 426 body carries where to get the new build, when the deployment
    /// knows. Absent on a homelab server that isn't shipping through the store.
    static func upgradeURL(from data: Data) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return object["upgrade_url"] as? String
    }
}

/// Library ordering (issue #13). `title` is the API default and is left out of
/// the query so old backends keep working.
enum RecipeSort: String, CaseIterable, Sendable {
    case title
    case mostCooked = "most_cooked"
    case leastRecentlyCooked = "least_recently_cooked"

    var label: String {
        switch self {
        case .title: "A–Z"
        case .mostCooked: "Most cooked"
        case .leastRecentlyCooked: "Not had in a while"
        }
    }
}

// MARK: - Endpoint helpers

extension APIClient {
    /// Unauthenticated, and never gated by the server's own client check —
    /// safe to call before login and from a blocked build.
    func clientConfig() async throws -> ClientConfig {
        try await send("GET", "/client-config", as: ClientConfig.self)
    }

    func login(email: String, password: String) async throws -> AuthResponse {
        try await send("POST", "/auth/login", json: ["email": email, "password": password], as: AuthResponse.self)
    }

    /// `inviteCode` nil (or empty) creates a household of your own; supplying one
    /// joins the household that issued it. `raw` strips nil values, so an
    /// omitted code never reaches the wire.
    func register(
        email: String,
        password: String,
        displayName: String,
        inviteCode: String? = nil
    ) async throws -> AuthResponse {
        try await send(
            "POST", "/auth/register",
            json: [
                "email": email,
                "password": password,
                "display_name": displayName,
                "invite_code": inviteCode?.isEmpty == false ? inviteCode : nil,
            ],
            as: AuthResponse.self
        )
    }

    /// Returns a fresh session token: the server revokes every existing one.
    func changePassword(currentPassword: String, newPassword: String) async throws -> AuthResponse {
        try await send(
            "POST", "/auth/password",
            json: ["current_password": currentPassword, "new_password": newPassword],
            as: AuthResponse.self
        )
    }

    func me() async throws -> UserProfile {
        try await send("GET", "/auth/me", as: UserProfile.self)
    }

    func ingredient(id: UUID) async throws -> IngredientInfo {
        try await send("GET", "/ingredients/\(id.uuidString.lowercased())", as: IngredientInfo.self)
    }

    func updateIngredient(
        id: UUID,
        aisle: String? = nil,
        isStaple: Bool? = nil,
        valueTier: ValueTier? = nil,
        valueNote: String? = nil
    ) async throws -> IngredientInfo {
        var payload: [String: Any?] = [:]
        if let aisle { payload["aisle"] = aisle }
        if let isStaple { payload["is_staple"] = isStaple }
        if let valueTier { payload["value_tier"] = valueTier.rawValue }
        if let valueNote { payload["value_note"] = valueNote }  // "" clears the note server-side
        return try await send(
            "PATCH", "/ingredients/\(id.uuidString.lowercased())", json: payload, as: IngredientInfo.self
        )
    }

    func recipes(search: String?, sort: RecipeSort = .title) async throws -> [RecipeSummary] {
        var query: [URLQueryItem] = []
        if let search, !search.isEmpty { query.append(URLQueryItem(name: "search", value: search)) }
        if sort != .title { query.append(URLQueryItem(name: "sort", value: sort.rawValue)) }
        return try await send("GET", "/recipes", query: query, as: [RecipeSummary].self)
    }

    func recipe(id: UUID) async throws -> Recipe {
        try await send("GET", "/recipes/\(id.uuidString.lowercased())", as: Recipe.self)
    }

    /// 409 when a meal still uses the recipe — the detail names the way out.
    func deleteRecipe(id: UUID) async throws {
        try await raw("DELETE", "/recipes/\(id.uuidString.lowercased())")
    }

    func ingest(url: String) async throws -> IngestResponse {
        try await send("POST", "/recipes/ingest", json: ["url": url], as: IngestResponse.self)
    }

    func meals() async throws -> [Meal] {
        try await send("GET", "/meals", as: [Meal].self)
    }

    /// The API takes recipes either as bare `recipe_ids` (all ×1) or as
    /// `recipes: [{recipe_id, scale}]`, and rejects both together. Always send
    /// the richer shape: an unscaled meal is just every scale at 1.
    private static func recipePayload(_ recipeIds: [UUID], _ scales: [UUID: Double]) -> [[String: Any]] {
        recipeIds.map { id in
            ["recipe_id": id.uuidString.lowercased(), "scale": scales[id] ?? 1]
        }
    }

    private static func linePayload(_ lines: [LooseLine]) -> [[String: Any]] {
        lines.map { line in
            var entry: [String: Any] = ["name": line.name]
            if let quantity = line.quantity { entry["quantity"] = quantity }
            if let unit = line.unit { entry["unit"] = unit }
            return entry
        }
    }

    func createMeal(
        name: String,
        slot: String?,
        recipeIds: [UUID],
        scales: [UUID: Double] = [:],
        looseIngredients: [LooseLine] = []
    ) async throws -> Meal {
        try await send(
            "POST", "/meals",
            json: [
                "name": name,
                "slot": slot,
                "recipes": Self.recipePayload(recipeIds, scales),
                "loose_ingredients": Self.linePayload(looseIngredients),
            ],
            as: Meal.self
        )
    }

    /// A true PATCH: only the arguments passed are sent, so a rename can't
    /// silently blank the meal's composition. Passing recipeIds or
    /// looseIngredients replaces that whole list (the API's contract) and
    /// re-syncs the active shopping list server-side.
    func updateMeal(
        id: UUID,
        name: String? = nil,
        slot: String? = nil,
        recipeIds: [UUID]? = nil,
        scales: [UUID: Double] = [:],
        looseIngredients: [LooseLine]? = nil
    ) async throws -> Meal {
        var payload: [String: Any?] = [:]
        if let name { payload["name"] = name }
        if let slot { payload["slot"] = slot }
        if let recipeIds { payload["recipes"] = Self.recipePayload(recipeIds, scales) }
        if let looseIngredients { payload["loose_ingredients"] = Self.linePayload(looseIngredients) }
        return try await send("PATCH", "/meals/\(id.uuidString.lowercased())", json: payload, as: Meal.self)
    }

    /// JSON `null` rather than "key absent" — see `updateRecipe`.
    private static func nullable(_ value: (some Any)?) -> Any {
        value ?? NSNull()
    }

    /// Correct a parsed recipe (#29). Only the arguments passed are sent: the
    /// endpoint is a true PATCH, and sending an untouched field would mark the
    /// recipe edited for no reason. Passing `ingredients` replaces the whole
    /// list and re-syncs the shopping list for every meal using the recipe,
    /// so only send it when a line actually changed.
    func updateRecipe(
        id: UUID,
        title: String? = nil,
        servings: Int?? = nil,
        prepMinutes: Int?? = nil,
        cookMinutes: Int?? = nil,
        imageUrl: String?? = nil,
        instructions: String?? = nil,
        tags: [String]? = nil,
        ingredients: [LooseLine]? = nil
    ) async throws -> Recipe {
        // Each of these is doubly optional: the outer nil means "not touched,
        // don't send it", the inner nil means "the user cleared it, send an
        // explicit null". `nullable` keeps the second case alive — a plain nil
        // in a `[String: Any?]` removes the key, which the server would read
        // as "leave it as it was".
        var payload: [String: Any?] = [:]
        if let title { payload["title"] = title }
        if let servings { payload["servings"] = Self.nullable(servings) }
        if let prepMinutes { payload["prep_minutes"] = Self.nullable(prepMinutes) }
        if let cookMinutes { payload["cook_minutes"] = Self.nullable(cookMinutes) }
        if let imageUrl { payload["image_url"] = Self.nullable(imageUrl) }
        if let instructions { payload["instructions"] = Self.nullable(instructions) }
        if let tags { payload["tags"] = tags }
        if let ingredients { payload["ingredients"] = Self.linePayload(ingredients) }
        return try await send("PATCH", "/recipes/\(id.uuidString.lowercased())", json: payload, as: Recipe.self)
    }

    func deleteMeal(id: UUID) async throws {
        try await raw("DELETE", "/meals/\(id.uuidString.lowercased())")
    }

    func currentPlan() async throws -> Plan {
        try await send("GET", "/plans/current", as: Plan.self)
    }

    func createPlan(label: String, copyFrom: UUID? = nil) async throws -> Plan {
        try await send(
            "POST", "/plans",
            json: ["label": label, "copy_from_plan_id": copyFrom?.uuidString.lowercased()],
            as: Plan.self
        )
    }

    func plans() async throws -> [PlanSummary] {
        try await send("GET", "/plans", as: [PlanSummary].self)
    }

    func archivePlan(id: UUID) async throws -> Plan {
        try await send("POST", "/plans/\(id.uuidString.lowercased())/archive", as: Plan.self)
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
    func patchItem(id: UUID, checked: Bool?, excluded: Bool?, stapleNeeded: Bool?) async throws -> ListItem
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

    func patchItem(id: UUID, checked: Bool?, excluded: Bool?, stapleNeeded: Bool?) async throws -> ListItem {
        try await send(
            "PATCH", "/shopping-list/items/\(id.uuidString.lowercased())",
            json: ["checked": checked, "excluded": excluded, "staple_needed": stapleNeeded],
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
