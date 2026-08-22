import XCTest
@testable import Meals

/// The app must never become a shop (issue #100, planning/08-freemium.md §6).
///
/// Guideline 3.1.3(f) exempts a free app acting as a companion to a paid web
/// tool "provided there is no purchasing inside the app, or calls to action for
/// purchase outside of the app". The first half is easy and permanent. The
/// second half is a property of every sentence the *server* can make this app
/// render, and of how the app chooses to render it.
///
/// So two things are pinned here, and both are about the app's behaviour rather
/// than about any particular wording:
///
/// 1. A billing refusal is an ordinary error, shown verbatim and inline. It is
///    not special-cased, which is exactly why every build already in the wild
///    handles one correctly: 402 and 403 fall through to `.server`, the same as
///    a 409 always has.
/// 2. Only a 426 can put the app behind `UpgradeRequiredView`. A cap that
///    blanked the app would read as broken functionality and invite the 2.1
///    rejection this app has already had once.
final class CommerceFreeTests: XCTestCase {
    private func client(protocolClass: AnyClass) -> APIClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [protocolClass]
        return APIClient(
            baseURL: URL(string: "http://testserver")!,
            token: "meals_test-token",
            session: URLSession(configuration: config)
        )
    }

    /// The exact sentence the server sends for a tier cap. Not asserted on for
    /// its wording — that is the server's test — but used here so what the app
    /// renders is a real one rather than a convenient invention.
    private static let capDetail = """
        This server's free tier allows 50 recipes per household, and this household has 50. \
        Nothing has been removed and everything already here still works, so this only stops it growing. \
        Delete a recipe nobody cooks (DELETE /recipes/{recipe_id}) to make room for this one.
        """

    private static let ceilingDetail = """
        This server allows at most 5,000 recipes per household, and this household has 5,000. \
        No tier on this server goes beyond that, so it is not something this household can change — \
        going further needs a word with whoever runs the server.
        """

    func testTierCapIsAnOrdinaryErrorShownVerbatim() async {
        StubProtocol.handler = { _ in
            (402, Data(#"{"detail": "\#(Self.capDetail)", "resource": "recipes", "limit": 50}"#.utf8))
        }
        do {
            _ = try await client(protocolClass: StubProtocol.self).fetchAisles()
            XCTFail("expected an error")
        } catch let error as APIError {
            // Not .upgradeRequired, and not a case of its own: the app has no
            // idea this was about money, which is the point.
            XCTAssertEqual(error, .server(status: 402, detail: Self.capDetail))
            XCTAssertEqual(error.errorDescription, Self.capDetail)
        } catch {
            XCTFail("unexpected error type: \(error)")
        }
    }

    func testFairUseCeilingIsTheSame() async {
        StubProtocol.handler = { _ in
            (403, Data(#"{"detail": "\#(Self.ceilingDetail)", "resource": "recipes", "limit": 5000}"#.utf8))
        }
        do {
            _ = try await client(protocolClass: StubProtocol.self).fetchAisles()
            XCTFail("expected an error")
        } catch let error as APIError {
            XCTAssertEqual(error, .server(status: 403, detail: Self.ceilingDetail))
        } catch {
            XCTFail("unexpected error type: \(error)")
        }
    }

    func testAFullServerIsAlsoJustAnError() async {
        // MAX_HOUSEHOLDS / MAX_USERS answer 503 (issue #96). Nothing about it is
        // a purchase either, so it must not be special-cased into one.
        let detail = "This server is full: it holds at most 25 households and has 25."
        StubProtocol.handler = { _ in (503, Data(#"{"detail": "\#(detail)"}"#.utf8)) }
        do {
            _ = try await client(protocolClass: StubProtocol.self).fetchAisles()
            XCTFail("expected an error")
        } catch let error as APIError {
            XCTAssertEqual(error, .server(status: 503, detail: detail))
        } catch {
            XCTFail("unexpected error type: \(error)")
        }
    }

    /// The blocker is reachable from exactly one status code. If a future change
    /// routes anything else into `Session.upgrade`, this fails.
    func testOnlyAnUpgradeRequiredResponseCanBlankTheApp() async {
        for status in [402, 403, 409, 503] {
            var blocked = false
            let observer = NotificationCenter.default.addObserver(
                forName: .mealsUpgradeRequired, object: nil, queue: nil
            ) { _ in blocked = true }
            defer { NotificationCenter.default.removeObserver(observer) }

            StubProtocol.handler = { _ in (status, Data(#"{"detail": "nope"}"#.utf8)) }
            _ = try? await client(protocolClass: StubProtocol.self).fetchAisles()
            XCTAssertFalse(blocked, "status \(status) must not put the app behind the upgrade screen")
        }
    }

    func testAnUpgradeRequiredResponseStillDoes() async {
        // The control: the one status that is allowed to blank the app.
        var blocked = false
        let observer = NotificationCenter.default.addObserver(
            forName: .mealsUpgradeRequired, object: nil, queue: nil
        ) { _ in blocked = true }
        defer { NotificationCenter.default.removeObserver(observer) }

        StubProtocol.handler = { _ in (426, Data(#"{"detail": "update to keep using Meals"}"#.utf8)) }
        _ = try? await client(protocolClass: StubProtocol.self).fetchAisles()
        XCTAssertTrue(blocked)
    }
}
