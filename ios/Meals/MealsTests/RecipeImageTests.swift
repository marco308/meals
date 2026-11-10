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

    /// The same guard decides whether "Open original recipe" gets a link:
    /// `source_url` is stored as sent, and a `Link` would hand any scheme to
    /// the system (#257).
    func testSourceLinksAreHeldToTheSameRule() {
        XCTAssertEqual(WebURL.parse("https://www.bbc.co.uk/food/recipes/x")?.host, "www.bbc.co.uk")
        for raw in ["javascript:alert(1)", "myapp://open", "file:///etc/passwd", "not a url", ""] {
            XCTAssertNil(WebURL.parse(raw), "\(raw) should not be a link")
        }
    }
}
