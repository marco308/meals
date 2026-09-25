import Foundation
import Security

/// The session token and the server that issued it. The token is only ever
/// sent back to that server (`Session.token`), because the keychain outlives
/// deleting the app and the server field doesn't.
struct Credential: Codable, Equatable, Sendable {
    let token: String
    /// `Session.origin(of:)` of the server the token came from.
    let origin: String
}

enum StoredCredential: Equatable, Sendable {
    case current(Credential)
    /// A bare token, saved by a build that didn't record where it came from.
    case legacy(token: String)
}

/// Where the credential lives. The keychain in the app; memory in tests, so a
/// test run never touches (or signs out) the simulator's real session.
protocol CredentialStore: Sendable {
    func load() -> StoredCredential?
    func save(_ credential: Credential)
    func delete()
}

/// Minimal keychain wrapper for the credential, the one secret the app holds.
/// Falls back to deleting on overwrite for simplicity.
struct KeychainStore: CredentialStore {
    private static let service = "com.marcuslab.meals"
    private static let account = "auth-token"

    func save(_ credential: Credential) {
        guard let data = try? JSONEncoder().encode(credential) else { return }
        delete()
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: Self.account,
            kSecValueData as String: data,
        ]
        SecItemAdd(query as CFDictionary, nil)
    }

    /// The same item older builds wrote a bare token into, so an update finds
    /// it: JSON for anything saved since, the raw token for anything before.
    func load() -> StoredCredential? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: Self.account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data
        else { return nil }
        if let credential = try? JSONDecoder().decode(Credential.self, from: data) {
            return .current(credential)
        }
        return String(data: data, encoding: .utf8).map { .legacy(token: $0) }
    }

    func delete() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: Self.account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
