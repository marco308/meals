import Foundation

/// The URLs the app points people at, and the server it starts out talking to.
///
/// Meals has no cloud: the app is a client for a server you run. That makes the
/// server URL a first-class piece of configuration rather than a debug setting,
/// and it makes "which server's privacy policy?" a real question — the answer is
/// whichever one is holding your data, so the policy and support links follow
/// the connected server rather than being hardcoded to mine.
enum AppLinks {
    /// Where a fresh install points. A public download that opens on a dead
    /// `localhost` looks broken, so this is a server that is actually up — but
    /// registration on it is closed, and the sign-in screen says so. Point the
    /// app at your own with the field there, or with `-serverURL` in a scheme
    /// argument during development.
    static let defaultServerURL = "https://meals.marcuslab.uk"

    static let sourceCode = URL(string: "https://github.com/marco308/meals")!

    /// The policy and support pages of whichever server you're using — every
    /// deployment serves them at `/privacy` and `/support`. Falls back to the
    /// default server when the field holds something unparseable, so the links
    /// in Settings are never dead.
    static func privacy(server: String) -> URL { page("/privacy", server: server) }
    static func support(server: String) -> URL { page("/support", server: server) }

    /// The AI-facing pages every deployment serves: the skill is an
    /// assistant's operating manual for this server, the prompt pack a
    /// paste-anywhere version. Linked next to API tokens, which is where
    /// they're needed.
    static func skill(server: String) -> URL { page("/skill", server: server) }
    static func promptPack(server: String) -> URL { page("/prompt-pack", server: server) }

    private static func page(_ path: String, server: String) -> URL {
        let trimmed = server.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let base = URL(string: trimmed), base.scheme != nil, base.host != nil else {
            return URL(string: defaultServerURL + path)!
        }
        return base.appendingPathComponent(path)
    }
}
