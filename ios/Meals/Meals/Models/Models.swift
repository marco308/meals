import Foundation

// DTOs mirroring the Meals API responses (snake_case JSON decoded with
// .convertFromSnakeCase). Timestamps stay as strings — the app never does
// date math on them, so decoding stays robust across backends.

struct UserProfile: Codable, Equatable, Sendable {
    let id: UUID
    let email: String
    let displayName: String
}

struct AuthResponse: Codable, Sendable {
    let token: String
    let user: UserProfile
}

struct RecipeSummary: Codable, Identifiable, Equatable, Sendable {
    let id: UUID
    let title: String
    let sourceUrl: String?
    let servings: Int?
    let prepMinutes: Int?
    let cookMinutes: Int?
    let tags: [String]
    // Optional so the app still decodes against a backend that predates the
    // cooked-history fields.
    var timesCooked: Int? = nil
    var lastCookedAt: String? = nil

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

struct RecipeLine: Codable, Identifiable, Equatable, Sendable {
    let ingredientId: UUID
    let name: String
    let aisle: String
    let isStaple: Bool
    let quantity: Double?
    let unit: String?
    let display: String
    let raw: String?

    var id: UUID { ingredientId }
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
    var sources: [ItemSource]

    var neededBy: [String] {
        Array(Set(sources.compactMap(\.mealName))).sorted()
    }

    var isNeededStaple: Bool { stapleNeeded ?? false }
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

/// Ingredient-level metadata (canonical name, aisle, staple flag) — shared
/// by every recipe line and list item that references the ingredient.
struct IngredientInfo: Codable, Identifiable, Equatable, Sendable {
    let id: UUID
    let name: String
    var aisle: String
    var aisleLabel: String
    var isStaple: Bool
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
    static let fallback = ["🥬", "🍞", "🥩", "🥛", "🥫", "🍝", "🌶️", "🥤", "🍫", "🧊", "🧴", "❓"]
}
