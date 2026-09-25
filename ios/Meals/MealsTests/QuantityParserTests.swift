import XCTest
@testable import Meals

/// Issue #30: the glued form ("frozen peas 200g") is what people type off a
/// packet, and it used to be folded into the *name* — minting a canonical
/// ingredient called "frozen peas 200g" with its own aisle and value tier.
final class QuantityParserTests: XCTestCase {
    func testGluedUnitAtTheEnd() {
        let (name, quantity, unit) = QuantityParser.parse("frozen peas 200g")
        XCTAssertEqual(name, "frozen peas")
        XCTAssertEqual(quantity, 200)
        XCTAssertEqual(unit, "g")
    }

    func testGluedUnitAtTheStart() {
        let (name, quantity, unit) = QuantityParser.parse("200g frozen peas")
        XCTAssertEqual(name, "frozen peas")
        XCTAssertEqual(quantity, 200)
        XCTAssertEqual(unit, "g")
    }

    func testTwoWordGluedForm() {
        let (name, quantity, unit) = QuantityParser.parse("peas 200g")
        XCTAssertEqual(name, "peas")
        XCTAssertEqual(quantity, 200)
        XCTAssertEqual(unit, "g")
    }

    func testDecimalGluedForm() {
        let (name, quantity, unit) = QuantityParser.parse("mince 1.5kg")
        XCTAssertEqual(name, "mince")
        XCTAssertEqual(quantity, 1.5)
        XCTAssertEqual(unit, "kg")
    }

    func testBareCountBecomesAnItemCount() {
        // Otherwise the whole string becomes an ingredient named "6 eggs".
        let (name, quantity, unit) = QuantityParser.parse("6 eggs")
        XCTAssertEqual(name, "eggs")
        XCTAssertEqual(quantity, 6)
        XCTAssertEqual(unit, "item")
    }

    func testSpacedFormsStillWork() {
        XCTAssertEqual(QuantityParser.parse("milk 2 l").name, "milk")
        XCTAssertEqual(QuantityParser.parse("2 tins chopped tomatoes").name, "chopped tomatoes")
    }

    func testPlainNameIsLeftAlone() {
        let (name, quantity, unit) = QuantityParser.parse("bin bags")
        XCTAssertEqual(name, "bin bags")
        XCTAssertNil(quantity)
        XCTAssertNil(unit)
    }

    func testSingleWordIsNeverSplit() {
        let (name, quantity, _) = QuantityParser.parse("milk")
        XCTAssertEqual(name, "milk")
        XCTAssertNil(quantity)
    }
}

/// The unit vocabulary is a convenience, and rejections must arrive at the
/// field rather than when the whole meal fails to save.
final class UnitVocabularyTests: XCTestCase {
    func testRejectedUnitsCarryTheConversion() {
        XCTAssertEqual(MealsUnits.rejection(for: "tbsp"), "1 tbsp = 15 ml")
        XCTAssertEqual(MealsUnits.rejection(for: "  CUP "), "1 cup = 240 ml")
    }

    func testAcceptedUnitsPassThrough() {
        XCTAssertNil(MealsUnits.rejection(for: "g"))
        XCTAssertNil(MealsUnits.rejection(for: "tin"))
        XCTAssertNil(MealsUnits.rejection(for: nil))
        // A unit this build has never heard of is the server's business, not
        // the app's — never a client-side block.
        XCTAssertNil(MealsUnits.rejection(for: "punnet"))
    }

    /// The server bans the plurals too, and "milk 4 pints" used to sail past
    /// this list into the queue, to be refused on replay and dropped.
    /// `backend/tests/unit/test_ios_units.py` holds the list to the server's.
    func testThePluralsTheServerBansAreRefusedHereToo() {
        for unit in [
            "teaspoons", "tablespoons", "ounces", "lbs", "pounds", "pints", "sticks", "quart", "gallon",
            "fl oz", "floz", "PINTS",
        ] {
            XCTAssertNotNil(MealsUnits.rejection(for: unit), unit)
        }
        XCTAssertEqual(MealsUnits.rejection(for: "pints"), "1 UK pint = 568 ml")
        XCTAssertEqual(MealsUnits.rejection(for: "sticks"), "1 stick of butter = 113 g")
    }

    func testOnlyAWordCanBeAUnit() {
        // The server takes any word as a natural unit and nothing that isn't one.
        XCTAssertNotNil(MealsUnits.rejection(for: "l."))
        XCTAssertNotNil(MealsUnits.rejection(for: "2kg"))
        XCTAssertNotNil(MealsUnits.rejection(for: "-"))
        XCTAssertNil(MealsUnits.rejection(for: "gousse"))
        XCTAssertNil(MealsUnits.rejection(for: "tea bag"))
    }

    func testAnAmountHasToBeAboveZero() {
        for amount in [0, -1, Double.infinity, -Double.infinity, Double.nan] {
            XCTAssertNotNil(MealsUnits.rejection(forAmount: amount), "\(amount)")
        }
        XCTAssertNil(MealsUnits.rejection(forAmount: 0.5))
        XCTAssertNil(MealsUnits.rejection(forAmount: nil))
    }

    func testAmountTextNeverTraps() {
        XCTAssertEqual(MealsUnits.amountText(2), "2")
        XCTAssertEqual(MealsUnits.amountText(1.5), "1.5")
        XCTAssertEqual(MealsUnits.amountText(-0.0), "0")
        XCTAssertEqual(MealsUnits.amountText(1e19), "1e+19")
        XCTAssertEqual(MealsUnits.amountText(.infinity), "inf")
        XCTAssertEqual(Double(MealsUnits.amountText(1e19)), 1e19, "the editor has to read back what it wrote")
    }
}
