import XCTest
@testable import Meals

final class CookVoiceTests: XCTestCase {
    private func voice(_ id: String, _ name: String, _ language: String = "en-GB", _ quality: CookVoice.Quality = .standard) -> CookVoice {
        CookVoice(id: id, name: name, language: language, quality: quality)
    }

    func testBestQualityComesFirstThenModernThenThePhonesAccent() {
        let voices = [
            voice("com.apple.eloquence.en-GB.Eddy", "Eddy"),
            voice("com.apple.voice.compact.en-US.Samantha", "Samantha", "en-US"),
            voice("com.apple.voice.compact.en-GB.Daniel", "Daniel"),
            voice("com.apple.voice.enhanced.en-GB.Serena", "Serena", "en-GB", .enhanced),
            voice("com.apple.voice.premium.en-US.Zoe", "Zoe", "en-US", .premium),
            voice("com.apple.voice.compact.fr-FR.Thomas", "Thomas", "fr-FR"),
        ]
        XCTAssertEqual(CookVoice.ranked(voices, for: "en-GB").map(\.name), ["Zoe", "Serena", "Daniel", "Samantha", "Eddy"])
    }

    func testAChosenVoiceThatIsGoneFallsBackToTheBestAndSaysSo() {
        let installed = [voice("a", "Daniel"), voice("b", "Serena", "en-GB", .enhanced)]
        XCTAssertEqual(CookVoice.resolve(chosen: "a", from: installed, language: "en-GB").voice?.name, "Daniel")
        let missing = CookVoice.resolve(chosen: "gone", from: installed, language: "en-GB")
        XCTAssertEqual(missing.voice?.name, "Serena")
        XCTAssertTrue(missing.chosenIsMissing)
        XCTAssertEqual(CookVoice.resolve(chosen: nil, from: installed, language: "en-GB").voice?.name, "Serena")
    }

    func testOnlyBasicVoicesAreWorthADownloadHint() {
        XCTAssertTrue(CookVoice.needsDownload([voice("a", "Daniel")]))
        XCTAssertFalse(CookVoice.needsDownload([voice("a", "Daniel"), voice("b", "Serena", "en-GB", .enhanced)]))
        XCTAssertTrue(CookVoice.needsDownload([voice("c", "Amélie", "fr-FR", .premium)]))
    }

    func testANonEnglishPhoneHearsBritishEnglish() {
        XCTAssertEqual(CookVoice.language(forPhone: "en-AU"), "en-AU")
        XCTAssertEqual(CookVoice.language(forPhone: "de-DE"), "en-GB")
    }
}

final class CookStepsDecodingTests: XCTestCase {
    func testARecipeWithStepsDecodes() throws {
        let json = """
        {"id": "7403ca25-2b81-4b03-a605-5f0be6382700", "title": "Chicken", "source_url": null, "servings": 4,
         "prep_minutes": 10, "cook_minutes": 25, "image_url": null, "instructions": "1. Melt the butter.",
         "tags": [], "parse_source": "manual", "edited": false, "ingredients": [],
         "steps": [{"number": 1, "section": "For the sauce", "text": "Melt the butter.", "spoken": "Melt the butter.",
                    "ingredients": [{"ingredient_id": "2f2efcd0-9e57-48c1-86bd-dd91ce2b8386", "name": "butter", "amount": "25 g"}]}],
         "steps_source": "ai", "steps_fingerprint": "abc", "steps_outdated": false,
         "steps_requested_at": "2026-10-25T16:25:12.123456", "a_field_from_the_future": 1}
        """
        let recipe = try APIClient.decoder().decode(Recipe.self, from: Data(json.utf8))
        XCTAssertEqual(recipe.cookSteps.first?.ingredients.first?.amount, "25 g")
        XCTAssertEqual(recipe.cookSteps.first?.section, "For the sauce")
        XCTAssertTrue(recipe.stepsWrittenByAssistant)
        XCTAssertEqual(
            CookModeView.serverDate(recipe.stepsRequestedAt ?? ""),
            try Date("2026-10-25T16:25:12Z", strategy: .iso8601)
        )
    }

    func testARecipeFromAServerBeforeCookModeHasNoSteps() throws {
        let json = """
        {"id": "7403ca25-2b81-4b03-a605-5f0be6382700", "title": "Chicken", "source_url": null, "servings": null,
         "prep_minutes": null, "cook_minutes": null, "image_url": null, "instructions": "Cook it.",
         "tags": [], "parse_source": "manual", "edited": false, "ingredients": []}
        """
        let recipe = try APIClient.decoder().decode(Recipe.self, from: Data(json.utf8))
        XCTAssertTrue(recipe.cookSteps.isEmpty)
        XCTAssertFalse(recipe.stepsWrittenByAssistant)
    }
}
