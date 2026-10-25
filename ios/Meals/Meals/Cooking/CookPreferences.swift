import Foundation
import Observation

/// How cook mode behaves on this iPhone: the voice, how fast it reads, and
/// whether moving to a step reads it. On this phone only, because which voices
/// are installed differs from one device to the next. A value that is the
/// default isn't stored, so nobody is pinned to today's defaults.
@MainActor
@Observable
final class CookPreferences {
    static let shared = CookPreferences()

    /// `AVSpeechSynthesisVoice.identifier`; nil is automatic. Kept even while
    /// its voice is missing, so it comes back when the voice does.
    var voiceID: String? { didSet { defaults.set(voiceID, forKey: Key.voice) } }
    /// A multiple of the system's default rate.
    var rate: Double {
        didSet { rate == 1 ? defaults.removeObject(forKey: Key.rate) : defaults.set(rate, forKey: Key.rate) }
    }
    var readEachStep: Bool { didSet { defaults.set(readEachStep, forKey: Key.readEachStep) } }

    static let rates: [Double] = [0.8, 0.9, 1, 1.1, 1.2]

    private enum Key {
        static let voice = "cook.voice"
        static let rate = "cook.rate"
        static let readEachStep = "cook.readEachStep"
    }

    @ObservationIgnored private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        voiceID = defaults.string(forKey: Key.voice)
        let stored = defaults.double(forKey: Key.rate)
        rate = Self.rates.contains(stored) ? stored : 1
        readEachStep = defaults.bool(forKey: Key.readEachStep)
    }
}
