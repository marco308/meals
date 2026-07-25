import XCTest
@testable import Meals

/// Decodes fixtures captured verbatim from the running backend (see
/// Fixtures/), so these fail if the API contract and the app's models drift.
final class ModelDecodingTests: XCTestCase {
    private func fixture(_ name: String) throws -> Data {
        let url = Bundle(for: ModelDecodingTests.self).url(forResource: name, withExtension: "json")
        return try Data(contentsOf: XCTUnwrap(url, "missing fixture \(name).json"))
    }

    func testDecodesShoppingList() throws {
        let payload = try APIClient.decoder().decode(ShoppingListPayload.self, from: fixture("shopping_list"))
        XCTAssertEqual(payload.status, "active")
        XCTAssertGreaterThan(payload.items.count, 10)

        let beef = try XCTUnwrap(payload.items.first { $0.name == "minced beef" })
        XCTAssertEqual(beef.quantity, 1000)
        XCTAssertEqual(beef.unit, "g")
        XCTAssertEqual(beef.display, "1 kg")
        XCTAssertEqual(beef.aisle, "🥩")
        XCTAssertFalse(beef.checked)
        XCTAssertEqual(beef.stapleNeeded, false)
        XCTAssertEqual(beef.sources.count, 2)
        XCTAssertEqual(
            beef.neededBy.sorted(),
            ["Cottage pie with peas & carrots", "Spag bol"]
        )
        XCTAssertTrue(beef.sources.allSatisfy { !$0.adHoc })
    }

    func testDecodesAdhocProvenance() throws {
        let payload = try APIClient.decoder().decode(ShoppingListPayload.self, from: fixture("shopping_list"))
        let milk = try XCTUnwrap(payload.items.first { $0.name == "milk" && $0.unit == "ml" })
        XCTAssertTrue(milk.sources.contains { $0.adHoc })
    }

    func testRecipeSourcesCarryIdsForNavigation() throws {
        let payload = try APIClient.decoder().decode(ShoppingListPayload.self, from: fixture("shopping_list"))
        let beef = try XCTUnwrap(payload.items.first { $0.name == "minced beef" })
        XCTAssertEqual(beef.sources.compactMap(\.recipeId).count, 2, "recipe sources link back to their recipes")
        XCTAssertEqual(beef.sources.compactMap(\.mealId).count, 2)
    }

    func testDecodesPlan() throws {
        let plan = try APIClient.decoder().decode(Plan.self, from: fixture("plan"))
        XCTAssertEqual(plan.label, "This week's options")
        XCTAssertEqual(plan.meals.count, 4)
        let slots = plan.slots.map(\.slot)
        XCTAssertEqual(slots, ["dinner", "lunch"])
        let dinner = try XCTUnwrap(plan.slots.first?.meals.first)
        XCTAssertNil(dinner.cookedAt)
    }

    func testDecodesRecipes() throws {
        let recipes = try APIClient.decoder().decode([RecipeSummary].self, from: fixture("recipes"))
        XCTAssertEqual(recipes.count, 3)
        let spag = try XCTUnwrap(recipes.first { $0.title == "Spaghetti Bolognese" })
        XCTAssertEqual(spag.servings, 4)
        XCTAssertEqual(spag.totalMinutes, 60)
    }

    func testDecodesRecipeDetail() throws {
        let recipe = try APIClient.decoder().decode(Recipe.self, from: fixture("recipe_detail"))
        XCTAssertFalse(recipe.ingredients.isEmpty)
        XCTAssertEqual(recipe.parseSource, "manual")
        for line in recipe.ingredients {
            XCTAssertFalse(line.name.isEmpty)
        }
    }

    func testDecodesMealsWithLooseIngredients() throws {
        let meals = try APIClient.decoder().decode([Meal].self, from: fixture("meals"))
        let cottage = try XCTUnwrap(meals.first { $0.name.contains("Cottage") })
        XCTAssertFalse(cottage.looseIngredients.isEmpty)
        XCTAssertEqual(cottage.recipes.count, 1)
    }

    func testDecodesIngredientInfo() throws {
        let info = try APIClient.decoder().decode(IngredientInfo.self, from: fixture("ingredient"))
        XCTAssertEqual(info.name, "tartare sauce")
        XCTAssertEqual(info.aisle, "❓")
        XCTAssertEqual(info.aisleLabel, "Unknown")
        XCTAssertFalse(info.isStaple)
    }

    func testDecodesValueTier() throws {
        let json = Data(
            """
            {"id":"61931d47-4154-418a-a43f-f734a0e3d888","name":"olive oil","aisle":"🍝",
             "aisle_label":"Dry goods & pasta","is_staple":true,"value_tier":"premium",
             "value_tier_label":"Worth paying up for","value_note":"the cheap stuff goes bitter"}
            """.utf8
        )
        let info = try APIClient.decoder().decode(IngredientInfo.self, from: json)
        XCTAssertEqual(info.tier, .premium)
        XCTAssertEqual(info.tier.badge, "⭐")
        XCTAssertEqual(info.valueNote, "the cheap stuff goes bitter")
    }

    func testUntaggedAndOlderPayloadsHaveNoValueOpinion() throws {
        // The fixtures predate the value tier — an older backend (or a cache
        // written by an older app) must still decode, with no advice shown.
        let info = try APIClient.decoder().decode(IngredientInfo.self, from: fixture("ingredient"))
        XCTAssertEqual(info.tier, .any)
        XCTAssertEqual(info.tier.badge, "")

        let payload = try APIClient.decoder().decode(ShoppingListPayload.self, from: fixture("shopping_list"))
        XCTAssertTrue(payload.items.allSatisfy { $0.tier == .any && $0.valueNote == nil })
    }

    func testDecodesAisles() throws {
        let aisles = try APIClient.decoder().decode([Aisle].self, from: fixture("aisles"))
        XCTAssertEqual(aisles.first?.emoji, "🥬")
        XCTAssertEqual(aisles.last?.emoji, "❓")
        XCTAssertEqual(aisles.count, 12)
    }
}
