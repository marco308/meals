import Foundation
import Observation

/// Auth state + API client factory. The token lives in the keychain; the
/// server URL in UserDefaults (editable on the login screen, so the same
/// build talks to localhost in development or the homelab deployment).
@MainActor
@Observable
final class Session {
    var serverURL: String {
        didSet {
            UserDefaults.standard.set(serverURL, forKey: "serverURL")
            // A verdict belongs to the server that gave it — pointing the app
            // at localhost must not inherit the homelab's floor, or vice versa.
            upgrade = .ok
            Task { await checkClientCompatibility() }
        }
    }

    private(set) var token: String?
    private(set) var user: UserProfile?

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
    // Written once in init, read once in deinit (which is nonisolated), so it
    // steps outside the actor rather than dragging the rest of Session with it.
    @ObservationIgnored private nonisolated(unsafe) var upgradeObserver: (any NSObjectProtocol)?

    var isAuthenticated: Bool { token != nil }

    init() {
        serverURL = UserDefaults.standard.string(forKey: "serverURL") ?? "http://localhost:8000"
        token = KeychainStore.loadToken()
        observeUpgradeNotices()
    }

    deinit {
        if let upgradeObserver { NotificationCenter.default.removeObserver(upgradeObserver) }
    }

    var api: APIClient {
        APIClient(baseURL: URL(string: serverURL) ?? URL(string: "http://localhost:8000")!, token: token)
    }

    func logIn(email: String, password: String) async throws {
        let auth = try await api.login(email: email, password: password)
        apply(auth)
    }

    func register(email: String, password: String, displayName: String) async throws {
        let auth = try await api.register(email: email, password: password, displayName: displayName)
        apply(auth)
    }

    /// Changing the password revokes every session token server-side, so the
    /// fresh one that comes back replaces what's in the keychain — this device
    /// stays logged in, others have to sign in again.
    func changePassword(current: String, new: String) async throws {
        let auth = try await api.changePassword(currentPassword: current, newPassword: new)
        apply(auth)
    }

    /// Restore the user profile for an existing keychain token; drops the
    /// session only if the server says the token is bad (not when offline).
    func restore() async {
        guard token != nil else { return }
        do {
            user = try await api.me()
        } catch APIError.unauthorized {
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
        token = nil
        user = nil
        KeychainStore.deleteToken()
    }

    private func apply(_ auth: AuthResponse) {
        token = auth.token
        user = auth.user
        KeychainStore.saveToken(auth.token)
    }
}
