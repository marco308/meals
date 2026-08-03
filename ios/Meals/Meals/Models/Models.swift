import Foundation

// DTOs mirroring the Meals API responses (snake_case JSON decoded with
// .convertFromSnakeCase). Timestamps stay as strings — the app never does
// date math on them, so decoding stays robust across backends.

struct UserProfile: Codable, Equatable, Sendable {
    let id: UUID
    let email: String
    let displayName: String
    /// The household this account is in (Q19: a server holds many). Optional
    /// because a build can outlive the server it talks to, and a server from
    /// before Q19 sends neither — a missing name must not be a decode error.
    let householdId: UUID?
    let householdName: String?
}

/// A single-use code that lets one more person register into this household
/// (`POST /auth/invites`). `code` comes back exactly once and is never
/// recoverable — the server stores only its hash.
struct InviteCreated: Codable, Sendable {
    let id: UUID
    let code: String
    /// A string, like every other timestamp here — the decoder has no date
    /// strategy on purpose, so a server that changes its date format can't
    /// turn a working screen into a decode failure.
    let expiresAt: String

    /// "2 August 2026", or nil if the timestamp isn't one we recognise — in
    /// which case the sheet says the code is single-use and leaves it there.
    var expiryLabel: String? {
        guard expiresAt.count >= 10 else { return nil }
        let parts = expiresAt.prefix(10).split(separator: "-")
        guard parts.count == 3, let year = Int(parts[0]), let month = Int(parts[1]), let day = Int(parts[2]),
              (1...12).contains(month)
        else { return nil }
        let names = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        return "\(day) \(names[month - 1]) \(year)"
    }
}

struct AuthResponse: Codable, Sendable {
    let token: String
    let user: UserProfile
}

/// Result of DELETE /auth/me. `householdDeleted` is true when the account was
/// the last member and the household's shared data went with it.
struct AccountDeleted: Codable, Sendable {
    let householdDeleted: Bool
    let detail: String
}

/// What the server expects of native clients (GET /client-config). Builds below
/// `minIosBuild` are refused with 426 on everything except the offline-queue
/// endpoints; builds below `currentIosBuild` just get a nudge.
struct ClientConfig: Codable, Equatable, Sendable {
    let apiVersion: String
    let minIosBuild: Int
    let currentIosBuild: Int
    let upgradeUrl: String?
}

struct RecipeSummary: Codable, Identifiable, Equatable, Sendable {
    let id: UUID
    let title: String
    let sourceUrl: String?
    let servings: Int?
    let prepMinutes: Int?
    let cookMinutes: Int?
    let tags: [String]
    /// Optional here as well as on `Recipe`: a backend that predates thumbnails
    /// on the listing simply doesn't send it.
    var imageUrl: String? = nil
    // Optional so the app still decodes against a backend that predates the
    // cooked-history fields.
    var timesCooked: Int? = nil
    var lastCookedAt: String? = nil
    /// Only present when the recipe is read as part of a meal: the batch-cooking
    /// multiple for *that* meal (#32). nil means ×1, which is what a backend
    /// without scaling means too.
    var scale: Double? = nil

    var totalMinutes: Int? {
        let total = (prepMinutes ?? 0) + (cookMinutes ?? 0)
        return total > 0 ? total : nil
    }

    var cookedSummary: String? { CookedHistory.summary(times: timesCooked, lastCookedAt: lastCookedAt) }
}

/// "cooked 12×" / "last cooked in May" — the phrasing for a recipe's or meal's
/// usage count (issue #13). Never says "never cooked": a library full of
/// zeroes shouldn't nag.
enum CookedHistory {
    static func summary(times: Int?, lastCookedAt: String?) -> String? {
        guard let times, times > 0 else { return nil }
        let count = "cooked \(times)×"
        guard let month = monthLabel(lastCookedAt) else { return count }
        return "\(count) · last \(month)"
    }

    /// Timestamps stay strings app-wide (see the note above), so this reads the
    /// ISO-8601 prefix rather than round-tripping through a formatter.
    static func monthLabel(_ timestamp: String?) -> String? {
        guard let timestamp, timestamp.count >= 7 else { return nil }
        let parts = timestamp.prefix(7).split(separator: "-")
        guard parts.count == 2, let year = Int(parts[0]), let month = Int(parts[1]), (1...12).contains(month) else {
            return nil
        }
        let names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return "\(names[month - 1]) \(year)"
    }
}

/// Is the posh version of an ingredient worth the money? Recorded once per
/// ingredient and surfaced where the decision is actually made — standing in
/// front of the shelf.
enum ValueTier: String, CaseIterable, Identifiable, Sendable {
    case premium
    case budget
    case any

    var id: String { rawValue }

    /// Tolerant of missing/unknown values so older caches and backends render.
    init(raw: String?) {
        self = ValueTier(rawValue: raw ?? "") ?? .any
    }

    var label: String {
        switch self {
        case .premium: "Worth paying up for"
        case .budget: "Own-brand is fine"
        case .any: "No strong opinion"
        }
    }

    var short: String {
        switch self {
        case .premium: "Premium"
        case .budget: "Budget"
        case .any: "No opinion"
        }
    }

    var badge: String {
        switch self {
        case .premium: "⭐"
        case .budget: "💷"
        case .any: ""
        }
    }
}

struct RecipeLine: Codable, Identifiable, Equatable, Sendable {
    let ingredientId: UUID
    let name: String
    let aisle: String
    let isStaple: Bool
    let quantity: Double?
    let unit: String?
    let display: String
    let raw: String?
    // Optional so responses from an older backend still decode.
    var valueTier: String? = nil
    var valueNote: String? = nil

    var id: UUID { ingredientId }
    var tier: ValueTier { ValueTier(raw: valueTier) }
}

struct Recipe: Codable, Identifiable, Equatable, Sendable {
    let id: UUID
    let title: String
    let sourceUrl: String?
    let servings: Int?
    let prepMinutes: Int?
    let cookMinutes: Int?
    let imageUrl: String?
    let instructions: String?
    let tags: [String]
    let parseSource: String
    let edited: Bool
    let ingredients: [RecipeLine]
    var timesCooked: Int? = nil
    var lastCookedAt: String? = nil

    var cookedSummary: String? { CookedHistory.summary(times: timesCooked, lastCookedAt: lastCookedAt) }
}

struct IngestResponse: Codable, Sendable {
    let recipe: Recipe
    let cached: Bool
}

struct Meal: Codable, Identifiable, Equatable, Sendable {
    let id: UUID
    let name: String
    let slot: String?
    let recipes: [RecipeSummary]
    let looseIngredients: [RecipeLine]
    var timesCooked: Int? = nil
    var lastCookedAt: String? = nil

    var cookedSummary: String? { CookedHistory.summary(times: timesCooked, lastCookedAt: lastCookedAt) }
}

struct PlanMeal: Codable, Identifiable, Equatable, Sendable {
    let id: UUID
    let meal: Meal
    let cookedAt: String?
}

struct Plan: Codable, Identifiable, Equatable, Sendable {
    let id: UUID
    let label: String
    let status: String
    let meals: [PlanMeal]

    var slots: [(slot: String, meals: [PlanMeal])] {
        let grouped = Dictionary(grouping: meals) { $0.meal.slot ?? "other" }
        return grouped.keys.sorted().map { (slot: $0, meals: grouped[$0] ?? []) }
    }
}

struct PlanSummary: Codable, Identifiable, Equatable, Hashable, Sendable {
    let id: UUID
    let label: String
    let startsOn: String?
    let status: String
    let mealCount: Int
}

struct ItemSource: Codable, Equatable, Sendable {
    let adHoc: Bool
    let mealName: String?
    let recipeTitle: String?
    let quantity: Double?
    // Optional so caches written by older app versions still decode.
    var mealId: UUID? = nil
    var recipeId: UUID? = nil
}

struct ListItem: Codable, Identifiable, Equatable, Sendable {
    var id: UUID
    var ingredientId: UUID
    var name: String
    var aisle: String
    var aisleLabel: String
    var isStaple: Bool
    var quantity: Double?
    var unit: String?
    var display: String
    var checked: Bool
    var excluded: Bool
    // Staple marked "I'm low" in the staples check — shown on the main list.
    // Optional so caches written by older app versions still decode.
    var stapleNeeded: Bool? = nil
    var valueTier: String? = nil
    var valueNote: String? = nil
    var sources: [ItemSource]

    var neededBy: [String] {
        Array(Set(sources.compactMap(\.mealName))).sorted()
    }

    var isNeededStaple: Bool { stapleNeeded ?? false }
    var tier: ValueTier { ValueTier(raw: valueTier) }
}

struct ShoppingListPayload: Codable, Equatable, Sendable {
    let id: UUID
    let status: String
    var items: [ListItem]
    let hiddenStaples: Int
}

struct Aisle: Codable, Equatable, Hashable, Sendable {
    let emoji: String
    let label: String
}

/// Ingredient-level metadata (canonical name, aisle, staple flag, premium-vs-
/// budget advice) — shared by every recipe line and list item that references
/// the ingredient.
struct IngredientInfo: Codable, Identifiable, Equatable, Sendable {
    let id: UUID
    let name: String
    var aisle: String
    var aisleLabel: String
    var isStaple: Bool
    // Optional so responses from an older backend still decode.
    var valueTier: String? = nil
    var valueNote: String? = nil

    var tier: ValueTier { ValueTier(raw: valueTier) }
}

/// A loose ingredient being written (meal sides, decision F1/F2): name plus
/// an optional quantity in the API's convention units.
struct LooseLine: Identifiable, Equatable, Sendable {
    let id = UUID()
    var name: String
    var quantity: Double?
    var unit: String?

    var display: String {
        let amount = ShoppingListStore.displayQuantity(quantity, unit)
        return amount.isEmpty ? name : "\(name) — \(amount)"
    }
}

// Fallback store-walking order used until /aisles has been fetched once.
enum AisleOrder {
    static let fallback = ["🥬", "🍞", "🥩", "❄️", "🥛", "🥫", "🍝", "🌶️", "🥤", "🍫", "🧊", "🧼", "🧴", "❓"]
}
