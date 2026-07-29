import XCTest

/// Captures the App Store screenshot set.
///
/// Screenshots are marketing material that has to be regenerated every time the
/// UI moves, so staging them by hand is a job you do once and then avoid. This
/// drives the real app against a real (locally seeded) API and writes full
/// device-resolution PNGs, which is what App Store Connect wants.
///
/// Driven by `make ios-screenshots` — it starts the API, seeds it, boots the
/// simulator and collects the files afterwards. Running this on its own does
/// nothing useful, which is why it lives in its own scheme and not in the one
/// `make ios-test` uses.
///
/// Files land in the test runner's Documents directory inside the simulator.
/// That is a real directory on the host, so the script fetches them with
/// `xcrun simctl get_app_container`; screenshots are also attached to the
/// result bundle, so a failed run can still be looked at in Xcode.
final class ScreenshotTests: XCTestCase {
    /// The throwaway API `capture.sh` starts. Hardcoded rather than passed in,
    /// because the first version of this test *was* parameterised, the value
    /// silently failed to arrive, and it fell back to port 8000 — where a real
    /// dev stack happened to be listening. The screenshots came out full of a
    /// real household's shopping. A fixed contract between the script and the
    /// test can't drift; an environment variable that goes missing can.
    private static let defaultServerURL = "http://127.0.0.1:8123"

    private var app: XCUIApplication!
    private var serverURL = ""
    private var captured = 0

    override func setUp() {
        super.setUp()
        continueAfterFailure = false
        serverURL = environment("SCREENSHOT_SERVER_URL", default: Self.defaultServerURL)
        app = XCUIApplication()
        app.launchArguments = ["-serverURL", serverURL]
        app.launch()
    }

    func testCaptureAppStoreScreenshots() throws {
        try signIn()

        // 1 — the plan. A pool of options, never a calendar (guiding principle 1).
        tab("Plan")
        waitForText("This week's options")
        capture("01-plan")

        // 2 — the shopping list, mid-shop. Ticking a few first is the honest
        // picture: aisle order only earns its keep once you're walking a shop.
        // Waiting for "Shopping" here proved nothing: the tab bar's own label
        // matches it from any screen, which is how this shot once shipped as a
        // second copy of the plan. A seeded row is proof the list rendered.
        tab("Shopping")
        waitForText("carrot")
        tickOffAFew()
        capture("02-shopping-list")

        // 3 — a recipe, reached the way a user reaches it.
        tab("Recipes")
        waitForText("Recipes")
        capture("04-recipe-library")

        let recipe = app.staticTexts["Spaghetti Bolognese"]
        if recipe.waitForExistence(timeout: 5) {
            recipe.tap()
            waitForText("Ingredients")
            capture("03-recipe-detail")
            app.navigationBars.buttons.firstMatch.tap()  // back
        }

        // 4 — settings: whose server this is, and the policy that follows it.
        tab("Settings")
        waitForText("Settings")
        // The screen that displays the server is the one place this test can
        // *prove* it shot the throwaway API rather than something real that
        // happened to answer on the same port. Check before the shutter.
        // Matched loosely on purpose: SwiftUI's LabeledContent folds its label
        // and value into one accessibility element, so an exact staticTexts
        // lookup for the URL finds nothing even when it's on screen.
        let showsServer = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@ OR value CONTAINS %@", serverURL, serverURL))
        XCTAssertGreaterThan(
            showsServer.count, 0,
            "Settings doesn't show \(serverURL) — the app is talking to something else, "
                + "and these screenshots could contain real data. Refusing to publish them."
        )
        capture("05-settings")

        XCTAssertEqual(captured, 5, "expected the full App Store set")
    }

    // MARK: - Steps

    private func signIn() throws {
        let email = app.textFields["Email"]
        guard email.waitForExistence(timeout: 20) else {
            // Already signed in from a previous run on this simulator — the
            // script erases the device, so this means the app didn't start.
            throw XCTSkip("the sign-in screen never appeared; is the app running?")
        }
        email.tap()
        email.typeText(environment("SCREENSHOT_EMAIL", default: "demo@example.com"))

        let password = app.secureTextFields["Password"]
        password.tap()
        password.typeText(environment("SCREENSHOT_PASSWORD", default: "demo-password-123"))

        app.buttons["Log in"].tap()

        XCTAssertTrue(
            app.tabBars.buttons["Plan"].waitForExistence(timeout: 30),
            "sign-in failed — is the seeded API running at \(serverURL)?"
        )
    }

    /// Tick a couple of things off, so the list looks like a shop in progress
    /// rather than a fresh export.
    private func tickOffAFew() {
        // Items from the first aisle, so the progress is visible in the part of
        // the list the screenshot actually shows.
        for name in ["broccoli", "carrot"] {
            let row = app.buttons.containing(.staticText, identifier: name).firstMatch
            if row.exists && row.isHittable { row.tap() }
        }
        Thread.sleep(forTimeInterval: 0.8)
    }

    // MARK: - Plumbing

    private func tab(_ name: String) {
        let button = app.tabBars.buttons[name]
        XCTAssertTrue(button.waitForExistence(timeout: 15), "no \(name) tab")
        button.tap()
        // A tap can land while the bar is still settling and switch nothing.
        // Selection state is the only proof the switch happened; retry once.
        if !waitUntilSelected(button) {
            button.tap()
            XCTAssertTrue(waitUntilSelected(button), "the \(name) tab never became selected")
        }
    }

    private func waitUntilSelected(_ button: XCUIElement, timeout: TimeInterval = 4) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if button.isSelected { return true }
            Thread.sleep(forTimeInterval: 0.2)
        }
        return button.isSelected
    }

    private func waitForText(_ text: String) {
        // Titles are the cheapest proof the screen finished rendering. Without
        // it a screenshot catches a spinner, and nobody notices until Apple does.
        let element = app.staticTexts[text].firstMatch
        _ = element.waitForExistence(timeout: 15)
        // Let animations settle: a half-drawn navigation transition is worse
        // than a missing screenshot, because it looks deliberate.
        Thread.sleep(forTimeInterval: 1.2)
    }

    private func capture(_ name: String) {
        let screenshot = XCUIScreen.main.screenshot()

        let attachment = XCTAttachment(screenshot: screenshot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)

        guard let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first else {
            return XCTFail("no Documents directory to write \(name) into")
        }
        let destination = directory.appendingPathComponent("\(name).png")
        do {
            try screenshot.pngRepresentation.write(to: destination)
            captured += 1
        } catch {
            XCTFail("could not write \(name).png: \(error)")
        }
    }

    private func environment(_ key: String, default fallback: String) -> String {
        let value = ProcessInfo.processInfo.environment[key] ?? ""
        return value.isEmpty ? fallback : value
    }
}
