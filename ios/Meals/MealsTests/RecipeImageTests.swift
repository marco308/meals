import XCTest
@testable import Meals

/// The photo URL comes from whatever a recipe site published or the user
/// typed, so the app decides for itself what it will load.
final class RecipeImageTests: XCTestCase {
    func testAcceptsHttpAndHttps() {
        XCTAssertNotNil(RecipeImageURL.parse("https://example.com/ragu.jpg"))
        XCTAssertNotNil(RecipeImageURL.parse("http://example.com/ragu.jpg"))
    }

    func testTrimsWhitespaceAroundAPastedURL() {
        XCTAssertEqual(
            RecipeImageURL.parse("  https://example.com/ragu.jpg\n")?.absoluteString,
            "https://example.com/ragu.jpg"
        )
    }

    func testRejectsAnythingTheAppWouldNotLoad() {
        for raw in ["", "   ", "example.com/ragu.jpg", "file:///etc/passwd", "javascript:alert(1)", "https://"] {
            XCTAssertNil(RecipeImageURL.parse(raw), "\(raw) should not be loaded")
        }
        XCTAssertNil(RecipeImageURL.parse(nil))
    }
}
