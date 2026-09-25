import Foundation
import Observation

/// Whose data a file on this device is: the server it came from, the account
/// that fetched it and the household it belongs to. The shopping list's cache
/// and queue are stamped with one, so nothing cached for one account is shown
/// to another, and nothing queued by one is ever sent as another.
struct DataOwner: Codable, Equatable, Hashable, Sendable {
    /// `Session.origin(of:)` of the server.
    let server: String
    let userId: UUID
    /// nil against a server from before households (Q19).
    let householdId: UUID?
}

/// Who is signed in, as far as data on the device is concerned. `owner` is nil
/// while signed in but not identified: a launch before the server has been
/// asked (or with no signal to ask it), or the moment after leaving a
/// household, before the server has said which one the account landed in.
struct AccountState: Equatable, Sendable {
    var signedIn: Bool
    var owner: DataOwner?

    static let signedOut = AccountState(signedIn: false, owner: nil)
}

/// Auth state + API client factory. The token lives in the keychain with the
/// server that issued it; the server URL in UserDefaults (editable on the
/// login screen, so the same build talks to localhost in development or the
/// homelab deployment).
@MainActor
@Observable
final class Session {
    var serverURL: String {
        didSet {
            defaults.set(serverURL, forKey: Self.serverURLKey)
            // A verdict belongs to the server that gave it — pointing the app
            // at localhost must not inherit the homelab's floor, or its ability
            // to send email, or vice versa.
            upgrade = .ok
            canResetPassword = true
            // So does a token: it counts only while the field points at the
            // server that issued it.
            publishAccountState()
            Task { await checkClientCompatibility() }
        }
    }

    /// What the keychain holds. It only counts while the app points at the
    /// server that issued it; see `token`.
    private(set) var credential: Credential?
    private(set) var user: UserProfile?

    /// The token, if it belongs to the server the app points at, and nil
    /// otherwise, so it is never sent anywhere else. The keychain survives
    /// deleting the app and the server field doesn't: a self-hoster who
    /// reinstalled would otherwise send their token to the default host.
    var token: String? {
        guard let credential, credential.origin == Self.origin(of: serverURL) else { return nil }
        return credential.token
    }

    /// Where this build stands against the server's expectations. `.required`
    /// blocks the UI; `.available` is a dismissible nudge.
    enum Upgrade: Equatable {
        case ok
        case available(url: String?)
        case required(detail: String, url: String?)

        init(config: ClientConfig, build: Int) {
            if build < config.minIosBuild {
                self = .required(
                    detail: "This version of Meals is too old for the server (it needs build "
                        + "\(config.minIosBuild), this is build \(build)). Update to carry on — anything "
                        + "you've ticked off or added is saved and will sync.",
                    url: config.upgradeUrl
                )
            } else if build < config.currentIosBuild {
                self = .available(url: config.upgradeUrl)
            } else {
                self = .ok
            }
        }

        /// A nudge can be waved away; a hard block can't be.
        var dismissingNudge: Self {
            if case .available = self { .ok } else { self }
        }
    }

    private(set) var upgrade: Upgrade = .ok

    /// Whether this server can send a reset code. Optimistic until told
    /// otherwise: the login screen is drawn before `/client-config` answers,
    /// and offering a button that turns out to be unavailable is a better
    /// failure than hiding one that would have worked.
    private(set) var canResetPassword = true

    /// Told, synchronously, whenever who is signed in (or into which household)
    /// changes. The shopping list's cache and queue follow it
    /// (`ShoppingListStore.accountChanged`), and so do the other caches.
    @ObservationIgnored var onAccountChange: ((_ old: AccountState, _ new: AccountState) -> Void)?
    /// Told when the account has been deleted, before the sign-out that
    /// follows, so what it left on this phone can go with it.
    @ObservationIgnored var onAccountDeleted: ((DataOwner?) -> Void)?
    @ObservationIgnored private var publishedState = AccountState.signedOut

    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let credentials: any CredentialStore
    @ObservationIgnored private let urlSession: URLSession

    // Written once in init, read once in deinit (which is nonisolated), so it
    // steps outside the actor rather than dragging the rest of Session with it.
    @ObservationIgnored private nonisolated(unsafe) var upgradeObserver: (any NSObjectProtocol)?

    private static let serverURLKey = "serverURL"

    var isAuthenticated: Bool { token != nil }

    /// Signed in or not, and as whom, for data on the device.
    var accountState: AccountState {
        guard token != nil else { return .signedOut }
        guard let user, let server = Self.origin(of: serverURL) else {
            return AccountState(signedIn: true, owner: nil)
        }
        return AccountState(
            signedIn: true, owner: DataOwner(server: server, userId: user.id, householdId: user.householdId)
        )
    }

    /// `appDataExisted` is whether this app's own files were already on the
    /// device at launch: how a token saved by an older build is told apart
    /// from one left behind by a deleted copy of the app.
    init(
        defaults: UserDefaults = .standard,
        credentials: any CredentialStore = KeychainStore(),
        appDataExisted: Bool = Session.appDataExists(),
        urlSession: URLSession = .shared
    ) {
        self.defaults = defaults
        self.credentials = credentials
        self.urlSession = urlSession
        // `-serverURL http://localhost:8000` as a launch argument lands in
        // UserDefaults, which is how the simulator gets pointed at a local API
        // without anyone typing into the field.
        serverURL = defaults.string(forKey: Self.serverURLKey) ?? AppLinks.defaultServerURL
        credential = Self.loadCredential(from: credentials, serverURL: serverURL, trustingLegacy: appDataExisted)
        observeUpgradeNotices()
    }

    deinit {
        if let upgradeObserver { NotificationCenter.default.removeObserver(upgradeObserver) }
    }

    var api: APIClient {
        APIClient(baseURL: Self.baseURL(serverURL), token: token, session: urlSession)
    }

    func logIn(email: String, password: String) async throws {
        let client = api
        let auth = try await client.login(email: email, password: password)
        apply(auth, from: client)
    }

    func register(
        email: String,
        password: String,
        displayName: String,
        inviteCode: String? = nil,
        householdName: String? = nil
    ) async throws {
        let client = api
        let auth = try await client.register(
            email: email,
            password: password,
            displayName: displayName,
            inviteCode: inviteCode,
            householdName: householdName
        )
        apply(auth, from: client)
    }

    /// Move this account into another household with an invite code (Q23). The
    /// token is unchanged, only which household it reads, so everything cached
    /// for the household we just left goes (`onAccountChange`). `password` is
    /// needed when this account is its household's only member.
    func joinHousehold(code: String, force: Bool = false, password: String? = nil) async throws {
        let token = self.token
        let profile = try await api.redeemInvite(code: code, force: force, password: password)
        guard self.token == token else { return }
        user = profile
        publishAccountState()
    }

    /// Remove someone from the household, or leave it by passing your own id.
    /// When it was you, the profile is refreshed so the app knows where it is.
    @discardableResult
    func removeMember(id: UUID) async throws -> MemberRemoved {
        let token = self.token
        let result = try await api.removeMember(id: id)
        if result.youLeft, self.token == token {
            // Out of the household everything on this phone belongs to, and
            // not yet told which one this account landed in: forget, then ask.
            user = nil
            publishAccountState()
            await restore()
        }
        return result
    }

    /// Changing the password revokes every session token server-side, so the
    /// fresh one that comes back replaces what's in the keychain — this device
    /// stays logged in, others have to sign in again.
    func changePassword(current: String, new: String) async throws {
        let client = api
        let auth = try await client.changePassword(currentPassword: current, newPassword: new)
        apply(auth, from: client)
    }

    func requestPasswordReset(email: String) async throws {
        try await api.requestPasswordReset(email: email)
    }

    /// Redeeming a reset code signs this device in with the fresh session the
    /// server hands back, so the user doesn't have to type the new password again.
    func confirmPasswordReset(code: String, newPassword: String) async throws {
        let client = api
        let auth = try await client.confirmPasswordReset(code: code, newPassword: newPassword)
        apply(auth, from: client)
    }

    /// Delete the account, then drop the local session. What the account left
    /// on this phone goes with it (`onAccountDeleted`), and only once the
    /// server has confirmed: a wrong password must not cost the offline queue.
    @discardableResult
    func deleteAccount(password: String) async throws -> AccountDeleted {
        let result = try await api.deleteAccount(password: password)
        onAccountDeleted?(accountState.owner)
        logOut()
        return result
    }

    /// Restore the user profile for an existing keychain token; drops the
    /// session only if the server says the token is bad (not when offline).
    func restore() async {
        guard let token else { return }
        do {
            let profile = try await api.me()
            // Signed out, or in as somebody else, while that was in flight:
            // the answer is about a session that has gone.
            guard self.token == token else { return }
            user = profile
            publishAccountState()
        } catch APIError.unauthorized {
            guard self.token == token else { return }
            logOut()
        } catch {
            // Offline or server unreachable — keep the session; cached data still works.
        }
    }

    // MARK: - Version alignment

    /// Ask the server what it expects of this build. Called at launch and on
    /// every foreground, so a deploy that raises the floor is noticed before
    /// the user hits a wall mid-task. Silent when offline — a server we can't
    /// reach can't refuse us either.
    func checkClientCompatibility() async {
        guard let config = try? await api.clientConfig() else { return }
        upgrade = Upgrade(config: config, build: ClientIdentity.buildNumber)
        // Absent means an older server that never published the key, and those
        // do send reset codes — so only an explicit false takes the button away.
        canResetPassword = config.passwordResetEnabled ?? true
    }

    func dismissUpgradeNudge() {
        upgrade = upgrade.dismissingNudge
    }

    private func observeUpgradeNotices() {
        upgradeObserver = NotificationCenter.default.addObserver(
            forName: .mealsUpgradeRequired, object: nil, queue: nil
        ) { [weak self] note in
            let detail = note.userInfo?["detail"] as? String ?? "This version of Meals is too old for the server."
            let url = note.userInfo?["upgradeUrl"] as? String
            Task { @MainActor in self?.upgrade = .required(detail: detail, url: url) }
        }
    }

    func logOut() {
        credential = nil
        user = nil
        credentials.delete()
        publishAccountState()
    }

    /// Bound to the server that issued it, which is the one the request went
    /// to: the field can be edited while a sign-in is in flight.
    private func apply(_ auth: AuthResponse, from client: APIClient) {
        guard let origin = client.baseURL.flatMap({ Self.origin(of: $0) }) else { return }
        let credential = Credential(token: auth.token, origin: origin)
        self.credential = credential
        user = auth.user
        credentials.save(credential)
        publishAccountState()
    }

    // MARK: - Who data on the device belongs to

    /// Tell `onAccountChange` when who is signed in, or into which household,
    /// has changed since it was last told. Synchronous on purpose: nothing can
    /// read the cached list or send the queue in between.
    private func publishAccountState() {
        let state = accountState
        guard state != publishedState else { return }
        let old = publishedState
        publishedState = state
        onAccountChange?(old, state)
    }

    /// The first word to `onAccountChange`, at launch, whatever the state: a
    /// signed-out launch still has to clear what an older build left behind.
    func announceAccountState() {
        publishedState = accountState
        onAccountChange?(.signedOut, publishedState)
    }

    /// A credential saved since tokens were bound to their server is taken as
    /// it is. A bare token from an older build is bound to the server field,
    /// which is where it came from as long as the app's own data (and with it
    /// the field) survived alongside it. When it didn't, the app was deleted
    /// and installed again: the keychain outlived the settings, nobody can say
    /// whose server the token is for, and it goes rather than be guessed at.
    private static func loadCredential(
        from store: any CredentialStore, serverURL: String, trustingLegacy: Bool
    ) -> Credential? {
        switch store.load() {
        case nil:
            return nil
        case .current(let credential)?:
            return credential
        case .legacy(let token)?:
            guard trustingLegacy, let origin = origin(of: serverURL) else {
                store.delete()
                return nil
            }
            let credential = Credential(token: token, origin: origin)
            store.save(credential)
            return credential
        }
    }

    /// Whether this app's files are already on the device. Checked in `init`,
    /// before anything at launch creates them; every build so far has made
    /// this folder on every launch.
    nonisolated static func appDataExists() -> Bool {
        FileManager.default.fileExists(atPath: DiskCache<Plan>.defaultDirectory().path(percentEncoded: false))
    }

    /// The server address as a URL to send requests to: http or https, with a
    /// host. nil for anything else, which the client refuses (`.invalidURL`)
    /// rather than quietly talking to the default server instead.
    nonisolated static func baseURL(_ address: String) -> URL? {
        let trimmed = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed),
              let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = url.host(percentEncoded: false), !host.isEmpty
        else { return nil }
        return url
    }

    /// `scheme://host[:port]`: what a token is bound to, and what data on the
    /// device is stamped with (`DataOwner.server`). nil for an address that
    /// isn't a usable server.
    nonisolated static func origin(of address: String) -> String? {
        baseURL(address).flatMap { origin(of: $0) }
    }

    nonisolated static func origin(of url: URL) -> String? {
        guard let scheme = url.scheme?.lowercased(),
              let host = url.host(percentEncoded: false)?.lowercased(), !host.isEmpty
        else { return nil }
        let defaultPort = scheme == "https" ? 443 : 80
        let port = url.port.map { $0 == defaultPort ? "" : ":\($0)" } ?? ""
        return "\(scheme)://\(host)\(port)"
    }
}
