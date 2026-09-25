import XCTest
@testable import Meals

/// A credential in memory, so no test touches the simulator's real keychain.
final class MemoryCredentials: CredentialStore, @unchecked Sendable {
    var stored: StoredCredential?

    init(_ stored: StoredCredential? = nil) {
        self.stored = stored
    }

    func load() -> StoredCredential? { stored }
    func save(_ credential: Credential) { stored = .current(credential) }
    func delete() { stored = nil }
}

/// The token goes back to the server that issued it and nowhere else. The
/// keychain survives deleting the app while the server field doesn't, so a
/// self-hoster who reinstalled used to send their token to the default host.
@MainActor
final class SessionTests: XCTestCase {
    // Read from inside the stub's handler, which runs off the main actor.
    private nonisolated static let userId = UUID(uuidString: "8B2E6E38-6B3E-4F45-9A6E-4C0C2F2D1A11")!
    private nonisolated static let homeId = UUID(uuidString: "1E4E9C1E-0A8E-4C60-9C2A-8E4E2B7D9C31")!
    private nonisolated static let otherHomeId = UUID(uuidString: "2F5FAD2F-1B9F-4D71-8D3B-9F5F3C8EAD42")!

    private nonisolated static func profile(household: UUID) -> String {
        """
        {"id": "\(userId.uuidString)", "email": "you@example.com", "display_name": "You",
         "created_at": "2026-07-25T20:00:00Z", "household_id": "\(household.uuidString)",
         "household_name": "Home"}
        """
    }

    override func setUp() {
        super.setUp()
        // Anything not stubbed by the test (a compatibility check the field
        // kicks off) gets a quick no rather than hanging.
        StubProtocol.handler = { _ in (404, Data(#"{"detail": "Not Found"}"#.utf8)) }
    }

    override func tearDown() {
        StubProtocol.handler = nil
        super.tearDown()
    }

    /// Its own defaults, so the simulator's real server field is never touched.
    private func makeSession(
        server: String?, credentials: MemoryCredentials, appDataExisted: Bool = true
    ) -> Session {
        let suite = "SessionTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        addTeardownBlock { UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite) }
        if let server { defaults.set(server, forKey: "serverURL") }
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return Session(
            defaults: defaults, credentials: credentials, appDataExisted: appDataExisted,
            urlSession: URLSession(configuration: config)
        )
    }

    // MARK: - Which server a token belongs to

    func testATokenIsOnlyUsedWithTheServerThatIssuedIt() {
        let credentials = MemoryCredentials(.current(Credential(token: "meals_home", origin: "https://meals.home.example")))
        // After a reinstall: the keychain kept the token, the field went back
        // to the default server.
        let session = makeSession(server: nil, credentials: credentials)
        XCTAssertEqual(session.serverURL, AppLinks.defaultServerURL)
        XCTAssertNil(session.token, "never sent to a server that didn't issue it")
        XCTAssertNil(session.api.token)
        XCTAssertFalse(session.isAuthenticated)

        session.serverURL = "https://meals.home.example/"
        XCTAssertEqual(session.token, "meals_home", "pointed back at its own server, it counts again")
    }

    func testAnOlderBuildsTokenIsBoundToTheServerItCameFrom() {
        // An update: the app's data (and so the server field) survived with it.
        let credentials = MemoryCredentials(.legacy(token: "meals_old"))
        let session = makeSession(server: "http://192.168.1.20:8000", credentials: credentials, appDataExisted: true)
        XCTAssertEqual(session.token, "meals_old", "updating the app doesn't sign anybody out")
        XCTAssertEqual(
            credentials.stored, .current(Credential(token: "meals_old", origin: "http://192.168.1.20:8000")),
            "and from now on the keychain says where it came from"
        )
    }

    func testATokenLeftBehindByADeletedCopyOfTheAppIsDropped() {
        // A reinstall of a build that saved a bare token: nothing says whose
        // server it's for, and guessing the default is exactly the leak.
        let credentials = MemoryCredentials(.legacy(token: "meals_old"))
        let session = makeSession(server: nil, credentials: credentials, appDataExisted: false)
        XCTAssertNil(session.token)
        XCTAssertNil(credentials.stored)
    }

    func testAnUnusableServerAddressIsAnErrorNotTheDefaultHost() async {
        StubProtocol.handler = { request in
            XCTFail("nothing should be sent anywhere, let alone \(request.url?.absoluteString ?? "?")")
            return (500, Data())
        }
        for address in ["meals.example.com", "ftp://meals.example.com", "", "https://"] {
            let session = makeSession(server: address, credentials: MemoryCredentials())
            do {
                try await session.logIn(email: "you@example.com", password: "a-strong-password")
                XCTFail("expected .invalidURL for \(address.debugDescription)")
            } catch {
                XCTAssertEqual(error as? APIError, .invalidURL, address.debugDescription)
            }
        }
    }

    func testSigningInBindsTheTokenToTheServerThatIssuedIt() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.url?.host()?.lowercased(), "meals.example.com")
            return (200, Data(#"{"token": "meals_new", "token_type": "bearer", "user": \#(Self.profile(household: Self.homeId))}"#.utf8))
        }
        let credentials = MemoryCredentials()
        let session = makeSession(server: "https://Meals.Example.com", credentials: credentials)
        var announced: [AccountState] = []
        session.onAccountChange = { _, new in announced.append(new) }

        try await session.logIn(email: "you@example.com", password: "a-strong-password")
        XCTAssertEqual(credentials.stored, .current(Credential(token: "meals_new", origin: "https://meals.example.com")))
        XCTAssertEqual(
            announced,
            [AccountState(signedIn: true, owner: DataOwner(server: "https://meals.example.com", userId: Self.userId, householdId: Self.homeId))],
            "the stores hear who signed in, once"
        )
    }

    func testOriginIsSchemeHostAndPort() {
        XCTAssertEqual(Session.origin(of: "https://Meals.Example.com/"), "https://meals.example.com")
        XCTAssertEqual(Session.origin(of: "https://meals.example.com:443"), "https://meals.example.com")
        XCTAssertEqual(Session.origin(of: "http://127.0.0.1:8000"), "http://127.0.0.1:8000")
        XCTAssertEqual(Session.origin(of: " https://meals.example.com "), "https://meals.example.com")
        XCTAssertNil(Session.origin(of: "meals.example.com"))
        XCTAssertNil(Session.origin(of: "ftp://meals.example.com"))
        XCTAssertNil(Session.origin(of: ""))
    }

    // MARK: - Telling the stores

    func testTheFirstWordAtLaunchIsSaidEvenWhenSignedOut() {
        // A signed-out launch still has to clear what an older build left behind.
        let session = makeSession(server: nil, credentials: MemoryCredentials())
        var heard: [(AccountState, AccountState)] = []
        session.onAccountChange = { heard.append(($0, $1)) }
        session.announceAccountState()
        XCTAssertEqual(heard.count, 1)
        XCTAssertEqual(heard.first?.1, .signedOut)
    }

    func testLeavingAHouseholdForgetsWhichOneUntilTheServerSays() async throws {
        let session = makeSession(
            server: "https://meals.example.com",
            credentials: MemoryCredentials(.current(Credential(token: "meals_t", origin: "https://meals.example.com")))
        )
        StubProtocol.handler = { _ in (200, Data(Self.profile(household: Self.homeId).utf8)) }
        await session.restore()

        var announced: [AccountState] = []
        session.onAccountChange = { _, new in announced.append(new) }
        StubProtocol.handler = { request in
            if request.httpMethod == "DELETE" {
                let body = #"{"removed_user_id": "\#(Self.userId.uuidString)", "you_left": true, "detail": "you have left"}"#
                return (200, Data(body.utf8))
            }
            return (200, Data(Self.profile(household: Self.otherHomeId).utf8))
        }
        try await session.removeMember(id: Self.userId)

        let server = "https://meals.example.com"
        XCTAssertEqual(announced, [
            AccountState(signedIn: true, owner: nil),
            AccountState(signedIn: true, owner: DataOwner(server: server, userId: Self.userId, householdId: Self.otherHomeId)),
        ], "the old household's list is set aside first, then the new one is known")
    }

    func testDeletingTheAccountTellsTheStoresBeforeSigningOut() async throws {
        let credentials = MemoryCredentials(.current(Credential(token: "meals_t", origin: "https://meals.example.com")))
        let session = makeSession(server: "https://meals.example.com", credentials: credentials)
        StubProtocol.handler = { _ in (200, Data(Self.profile(household: Self.homeId).utf8)) }
        await session.restore()

        var events: [String] = []
        session.onAccountDeleted = { owner in events.append("deleted \(owner?.userId == Self.userId)") }
        session.onAccountChange = { _, new in events.append(new.signedIn ? "signed in" : "signed out") }
        StubProtocol.handler = { _ in (200, Data(#"{"household_deleted": true, "detail": "account deleted"}"#.utf8)) }
        try await session.deleteAccount(password: "a-strong-password")

        XCTAssertEqual(events, ["deleted true", "signed out"])
        XCTAssertNil(credentials.stored)
    }

    func testAWrongPasswordDeletesNothingHere() async {
        let session = makeSession(
            server: "https://meals.example.com",
            credentials: MemoryCredentials(.current(Credential(token: "meals_t", origin: "https://meals.example.com")))
        )
        var deleted = false
        session.onAccountDeleted = { _ in deleted = true }
        StubProtocol.handler = { _ in (401, Data(#"{"detail": "that password is incorrect, so nothing was deleted"}"#.utf8)) }
        _ = try? await session.deleteAccount(password: "wrong")
        XCTAssertFalse(deleted, "the offline queue survives a mistyped password")
        XCTAssertTrue(session.isAuthenticated)
    }
}
