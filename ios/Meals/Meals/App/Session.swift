import Foundation
import Observation

/// Auth state + API client factory. The token lives in the keychain; the
/// server URL in UserDefaults (editable on the login screen, so the same
/// build talks to localhost in development or the homelab deployment).
@MainActor
@Observable
final class Session {
    var serverURL: String {
        didSet { UserDefaults.standard.set(serverURL, forKey: "serverURL") }
    }

    private(set) var token: String?
    private(set) var user: UserProfile?

    var isAuthenticated: Bool { token != nil }

    init() {
        serverURL = UserDefaults.standard.string(forKey: "serverURL") ?? "http://localhost:8000"
        token = KeychainStore.loadToken()
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
