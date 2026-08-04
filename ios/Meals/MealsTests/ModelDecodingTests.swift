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

    /// The listing fixture predates thumbnails; a summary that does carry one
    /// decodes it. Both shapes have to work — the app can outrun the server.
    func testSummaryPhotoIsOptionalAndDecodedWhenSent() throws {
        let recipes = try APIClient.decoder().decode([RecipeSummary].self, from: fixture("recipes"))
        XCTAssertNil(recipes.first?.imageUrl)

        let json = #"[{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "title": "Cottage Pie", "source_url": null, "servings": null, "prep_minutes": null, "cook_minutes": null, "image_url": "https://example.com/pie.jpg", "tags": []}]"#
        let withPhoto = try APIClient.decoder().decode([RecipeSummary].self, from: Data(json.utf8))
        XCTAssertEqual(withPhoto.first?.imageUrl, "https://example.com/pie.jpg")
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

    /// The fixtures predate the cooked-history fields, which is exactly the
    /// case that matters: a TestFlight build can reach a backend that hasn't
    /// been deployed yet, and it must still decode.
    func testCookedHistoryFieldsAreOptional() throws {
        let recipes = try APIClient.decoder().decode([RecipeSummary].self, from: fixture("recipes"))
        XCTAssertNil(recipes.first?.timesCooked)
        XCTAssertNil(recipes.first?.cookedSummary)
        let meals = try APIClient.decoder().decode([Meal].self, from: fixture("meals"))
        XCTAssertNil(meals.first?.timesCooked)
    }

    func testDecodesCookedHistoryWhenPresent() throws {
        let json = Data(
            #"""
            [{"id": "4f45efcd-6475-46aa-9668-34ec9c40103e", "title": "Spaghetti Bolognese",
              "source_url": null, "servings": 4, "prep_minutes": 15, "cook_minutes": 45,
              "tags": [], "times_cooked": 7, "last_cooked_at": "2026-06-14T18:20:00Z"}]
            """#.utf8
        )
        let recipes = try APIClient.decoder().decode([RecipeSummary].self, from: json)
        XCTAssertEqual(recipes.first?.timesCooked, 7)
        XCTAssertEqual(recipes.first?.cookedSummary, "cooked 7× · last Jun 2026")
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

    func testDecodesSupermarkets() throws {
        let json = Data(
            #"""
            [{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "name": "Big Tesco",
              "aisle_order": ["🍞", "🥬", "🥩"], "is_active": true, "created_at": "2026-08-01T10:00:00Z"}]
            """#.utf8
        )
        let markets = try APIClient.decoder().decode([Supermarket].self, from: json)
        XCTAssertEqual(markets.first?.name, "Big Tesco")
        XCTAssertEqual(markets.first?.aisleOrder, ["🍞", "🥬", "🥩"])
        XCTAssertEqual(markets.first?.isActive, true)
    }

    func testDecodesDuplicatesPayload() throws {
        let mint = #"{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "name": "mint", "aisle": "🥬", "aisle_label": "Fruit & veg", "is_staple": false, "value_tier": "any", "value_tier_label": "No strong opinion", "value_note": null}"#
        let leaves = #"{"id": "4f45efcd-6475-46aa-9668-34ec9c40103e", "name": "mint leaves", "aisle": "🥬", "aisle_label": "Fruit & veg", "is_staple": false, "value_tier": "any", "value_tier_label": "No strong opinion", "value_note": null}"#
        let json = Data(
            #"""
            {"groups": [{"canonical_name": "mint", "keeper": \#(mint), "duplicates": [\#(leaves)]}],
             "unfolded": [{"ingredient": \#(leaves), "canonical_name": "mint leaf"}]}
            """#.utf8
        )
        let payload = try APIClient.decoder().decode(DuplicatesPayload.self, from: json)
        XCTAssertEqual(payload.groups.first?.keeper.name, "mint")
        XCTAssertEqual(payload.groups.first?.duplicates.map(\.name), ["mint leaves"])
        XCTAssertEqual(payload.unfolded.first?.canonicalName, "mint leaf")
    }

    func testDecodesInvitesWithStatus() throws {
        let json = Data(
            #"""
            [{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "created_at": "2026-07-01T10:00:00Z",
              "expires_at": "2026-07-08T10:00:00Z", "accepted_at": null, "accepted_by_user_id": null},
             {"id": "4f45efcd-6475-46aa-9668-34ec9c40103e", "created_at": "2026-07-01T10:00:00Z",
              "expires_at": "2026-07-08T10:00:00Z", "accepted_at": "2026-07-02T09:00:00Z",
              "accepted_by_user_id": "61931d47-4154-418a-a43f-f734a0e3d888"}]
            """#.utf8
        )
        let invites = try APIClient.decoder().decode([InviteInfo].self, from: json)
        XCTAssertEqual(invites[0].status(now: "2026-07-03T10:00:00"), .open)
        XCTAssertEqual(invites[0].status(now: "2026-07-09T10:00:00"), .expired)
        XCTAssertEqual(invites[1].status(now: "2026-07-03T10:00:00"), .redeemed, "redeemed wins even before expiry")
        XCTAssertEqual(invites[1].status(now: "2026-07-09T10:00:00"), .redeemed, "…and after it")
    }

    func testDecodesAPITokens() throws {
        let json = Data(
            #"""
            [{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "kind": "pat", "label": "Claude on the laptop",
              "created_at": "2026-08-01T10:00:00Z", "expires_at": null, "last_used_at": "2026-08-02T09:30:00Z"}]
            """#.utf8
        )
        let tokens = try APIClient.decoder().decode([APIToken].self, from: json)
        XCTAssertEqual(tokens.first?.label, "Claude on the laptop")
        XCTAssertNil(tokens.first?.expiresAt)
        XCTAssertEqual(TimestampLabel.day(tokens.first?.lastUsedAt), "2 Aug 2026")
    }

    func testDecodesArchivedLists() throws {
        let json = Data(
            #"""
            [{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "created_at": "2026-07-26T10:00:00Z",
              "archived_at": "2026-08-02T17:00:00Z", "item_count": 14}]
            """#.utf8
        )
        let lists = try APIClient.decoder().decode([ArchivedListSummary].self, from: json)
        XCTAssertEqual(lists.first?.itemCount, 14)
        XCTAssertEqual(TimestampLabel.day(lists.first?.archivedAt), "2 Aug 2026")
    }

    /// The plan fixture predates nothing here, but a cache written by an older
    /// app has no archived_at key — that must stay decodable (the same rule as
    /// every other added field).
    func testPlanArchivedAtIsOptional() throws {
        let plan = try APIClient.decoder().decode(Plan.self, from: fixture("plan"))
        _ = plan.archivedAt  // present or not, never a decode error
    }

    func testDayLabelReadsTheIsoPrefixOnly() {
        XCTAssertEqual(TimestampLabel.day("2026-08-02T17:00:00Z"), "2 Aug 2026")
        XCTAssertEqual(TimestampLabel.day("2026-12-31"), "31 Dec 2026")
        XCTAssertNil(TimestampLabel.day(nil))
        XCTAssertNil(TimestampLabel.day("soon"))
        XCTAssertNil(TimestampLabel.day("2026-13-01T00:00:00Z"), "a nonsense month is not a date")
    }

    /// Only lines every source of which is ad-hoc offer delete — mirroring the
    /// server's `is_adhoc_only` so the app never offers a doomed action.
    func testAdhocOnlyMirrorsTheServerRule() {
        let byHand = ItemSource(adHoc: true, mealName: nil, recipeTitle: nil, quantity: 1)
        let fromMeal = ItemSource(adHoc: false, mealName: "Spag bol", recipeTitle: "Spag bol", quantity: 500)
        XCTAssertTrue(TestData.item(name: "bin bags", sources: [byHand]).isAdhocOnly)
        XCTAssertFalse(TestData.item(name: "beef", sources: [byHand, fromMeal]).isAdhocOnly)
    }
}
