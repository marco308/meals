import XCTest
@testable import Meals

/// The client half of the version-alignment gate (see backend app/client_gate.py).
final class ClientIdentityTests: XCTestCase {
    func testHeaderValueMatchesTheServersGrammar() {
        // The server parses `platform/version (build)` — anything else is
        // treated as an unidentified client and skips the gate entirely.
        XCTAssertEqual(ClientIdentity.headerValue(version: "0.1", build: "2"), "ios/0.1 (2)")
        XCTAssertEqual(ClientIdentity.headerValue(version: "1.2.3", build: "47"), "ios/1.2.3 (47)")
    }

    func testCurrentHeaderIsWellFormed() {
        XCTAssertTrue(ClientIdentity.current.hasPrefix("ios/"), ClientIdentity.current)
        XCTAssertTrue(ClientIdentity.current.hasSuffix(")"), ClientIdentity.current)
    }
}

final class ClientGateTests: XCTestCase {
    private func client(protocolClass: AnyClass) -> APIClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [protocolClass]
        return APIClient(
            baseURL: URL(string: "http://testserver")!,
            token: "meals_test-token",
            session: URLSession(configuration: config)
        )
    }

    func testEveryRequestIdentifiesTheBuild() async throws {
        StubProtocol.handler = { request in
            let header = request.value(forHTTPHeaderField: "X-Meals-Client")
            XCTAssertEqual(header, ClientIdentity.current)
            return (200, Data("[]".utf8))
        }
        _ = try await client(protocolClass: StubProtocol.self).fetchAisles()
    }

    func testUpgradeRequiredIsItsOwnError() async {
        let body = #"{"detail": "This version of Meals (build 2) is too old.", "min_ios_build": 5, "your_build": 2}"#
        StubProtocol.handler = { _ in (426, Data(body.utf8)) }
        do {
            _ = try await client(protocolClass: StubProtocol.self).fetchAisles()
            XCTFail("expected an error")
        } catch let error as APIError {
            XCTAssertEqual(error, .upgradeRequired(detail: "This version of Meals (build 2) is too old."))
        } catch {
            XCTFail("unexpected error type: \(error)")
        }
    }

    func testUpgradeRequiredBroadcastsSoAnyCallSiteTripsTheBlock() async {
        let notified = expectation(description: "upgrade notice posted")
        let observer = NotificationCenter.default.addObserver(
            forName: .mealsUpgradeRequired, object: nil, queue: nil
        ) { note in
            XCTAssertEqual(note.userInfo?["detail"] as? String, "too old")
            XCTAssertEqual(note.userInfo?["upgradeUrl"] as? String, "https://example.com/meals")
            notified.fulfill()
        }
        defer { NotificationCenter.default.removeObserver(observer) }

        StubProtocol.handler = { _ in
            (426, Data(#"{"detail": "too old", "upgrade_url": "https://example.com/meals"}"#.utf8))
        }
        _ = try? await client(protocolClass: StubProtocol.self).fetchAisles()
        await fulfillment(of: [notified], timeout: 2)
    }

    func testUpgradeURLIsOptional() {
        XCTAssertNil(APIClient.upgradeURL(from: Data(#"{"detail": "too old"}"#.utf8)))
        XCTAssertNil(APIClient.upgradeURL(from: Data("not json".utf8)))
    }

    func testClientConfigDecodes() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/client-config")
            return (
                200,
                Data(
                    #"{"api_version": "0.1.0", "min_ios_build": 2, "current_ios_build": 4, "upgrade_url": null}"#.utf8
                )
            )
        }
        let config = try await client(protocolClass: StubProtocol.self).clientConfig()
        XCTAssertEqual(
            config,
            ClientConfig(
                apiVersion: "0.1.0", minIosBuild: 2, currentIosBuild: 4, upgradeUrl: nil,
                passwordResetEnabled: nil
            )
        )
    }

    /// A server older than the field must decode, not throw: the config also
    /// carries the upgrade floor, so a strict decode here would blind the app
    /// to a hard block rather than merely to a button.
    func testClientConfigDecodesWithoutPasswordResetFlag() async throws {
        StubProtocol.handler = { _ in
            (
                200,
                Data(
                    #"{"api_version": "0.1.0", "min_ios_build": 2, "current_ios_build": 4, "upgrade_url": null}"#.utf8
                )
            )
        }
        let config = try await client(protocolClass: StubProtocol.self).clientConfig()
        XCTAssertNil(config.passwordResetEnabled)
    }

    func testClientConfigCarriesPasswordResetFlag() async throws {
        StubProtocol.handler = { _ in
            (
                200,
                Data(
                    #"{"api_version": "0.1.0", "min_ios_build": 0, "current_ios_build": 0, "upgrade_url": null, "password_reset_enabled": false}"#
                        .utf8
                )
            )
        }
        let config = try await client(protocolClass: StubProtocol.self).clientConfig()
        XCTAssertEqual(config.passwordResetEnabled, false)
    }
}

final class UpgradeStateTests: XCTestCase {
    private func config(min: Int, current: Int, url: String? = nil) -> ClientConfig {
        ClientConfig(
            apiVersion: "0.1.0", minIosBuild: min, currentIosBuild: current, upgradeUrl: url,
            passwordResetEnabled: nil
        )
    }

    func testBuildBelowTheFloorIsBlocked() {
        let state = Session.Upgrade(config: config(min: 5, current: 5, url: "https://example.com"), build: 2)
        guard case .required(let detail, let url) = state else { return XCTFail("expected .required, got \(state)") }
        XCTAssertTrue(detail.contains("build 5"))
        XCTAssertTrue(detail.contains("build 2"))
        XCTAssertEqual(url, "https://example.com")
    }

    func testBuildOnTheFloorButBehindTheShippedOneOnlyGetsANudge() {
        XCTAssertEqual(Session.Upgrade(config: config(min: 2, current: 4), build: 2), .available(url: nil))
    }

    func testCurrentBuildIsHappy() {
        XCTAssertEqual(Session.Upgrade(config: config(min: 2, current: 4), build: 4), .ok)
        // Running ahead of the store (a local build) is not a downgrade prompt.
        XCTAssertEqual(Session.Upgrade(config: config(min: 2, current: 4), build: 9), .ok)
    }

    func testAFloorOfZeroNeverBlocks() {
        XCTAssertEqual(Session.Upgrade(config: config(min: 0, current: 0), build: 0), .ok)
    }

    func testNudgeIsDismissibleButABlockIsNot() {
        XCTAssertEqual(Session.Upgrade.available(url: nil).dismissingNudge, .ok)
        let blocked = Session.Upgrade.required(detail: "too old", url: nil)
        XCTAssertEqual(blocked.dismissingNudge, blocked)
    }
}
