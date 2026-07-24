import XCTest
@testable import Meals

/// Covers #3: a recipe-only meal needs no typed name — the selected recipes'
/// titles become the meal name — while a recipe-less meal still requires one.
final class NewMealNameTests: XCTestCase {
    private func recipe(_ title: String) -> RecipeSummary {
        RecipeSummary(
            id: UUID(), title: title, sourceUrl: nil, servings: nil,
            prepMinutes: nil, cookMinutes: nil, tags: []
        )
    }

    func testTypedNameWinsOverSelection() {
        let feta = recipe("Crispy grilled feta")
        let resolved = NewMealView.resolvedName(
            typed: "  Feta night  ", selectedRecipes: [feta.id], library: [feta]
        )
        XCTAssertEqual(resolved, "Feta night")
    }

    func testEmptyNameFallsBackToRecipeTitle() {
        let feta = recipe("Crispy grilled feta with saucy butter beans")
        let resolved = NewMealView.resolvedName(
            typed: "", selectedRecipes: [feta.id], library: [feta]
        )
        XCTAssertEqual(resolved, "Crispy grilled feta with saucy butter beans")
    }

    func testWhitespaceOnlyNameCountsAsEmpty() {
        let feta = recipe("Crispy grilled feta")
        let resolved = NewMealView.resolvedName(
            typed: "   ", selectedRecipes: [feta.id], library: [feta]
        )
        XCTAssertEqual(resolved, "Crispy grilled feta")
    }

    func testMultipleRecipesJoinInLibraryOrder() {
        let pie = recipe("Cottage pie")
        let peas = recipe("Minted peas")
        // Selection is a Set — order must come from the library, not the set.
        let resolved = NewMealView.resolvedName(
            typed: "", selectedRecipes: [peas.id, pie.id], library: [pie, peas]
        )
        XCTAssertEqual(resolved, "Cottage pie + Minted peas")
    }

    func testNoNameAndNoSelectionStaysBlocked() {
        let resolved = NewMealView.resolvedName(
            typed: " ", selectedRecipes: [], library: [recipe("Anything")]
        )
        XCTAssertTrue(resolved.isEmpty, "empty resolved name keeps the save button disabled")
    }

    func testDerivedNameClampsToAPILimit() {
        let first = recipe(String(repeating: "a", count: 200))
        let second = recipe(String(repeating: "b", count: 200))
        let resolved = NewMealView.resolvedName(
            typed: "", selectedRecipes: [first.id, second.id], library: [first, second]
        )
        XCTAssertEqual(resolved.count, 300)
    }
}
