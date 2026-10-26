import AVFoundation
import Foundation

/// A voice cook mode can read steps in (Q29). The choosing is plain data
/// (unit-tested); the extension below reads the voices off the phone.
///
/// Ported from gymtrack's coach, which learned it first: the default voice on
/// most phones is a compact one and sounds horrible. The enhanced and premium
/// voices are free, offline and far more natural, but each has to be
/// downloaded in Settings, so cook mode picks the best one installed and says
/// how to get a better one.
struct CookVoice: Identifiable, Hashable, Sendable {
    enum Quality: Int, Comparable, CaseIterable, Sendable {
        case standard, enhanced, premium

        static func < (lhs: Self, rhs: Self) -> Bool { lhs.rawValue < rhs.rawValue }

        var title: String {
            switch self {
            case .standard: "Standard"
            case .enhanced: "Enhanced"
            case .premium: "Premium"
            }
        }
    }

    /// `AVSpeechSynthesisVoice.identifier`: what's remembered.
    let id: String
    let name: String
    /// "en-GB"
    let language: String
    let quality: Quality

    /// Recipes here are in English, so only English voices can read them.
    var isEnglish: Bool { language == "en" || language.hasPrefix("en-") }

    /// Eloquence (Eddy, Flo, Grandma…) and the old Mac voices are listed at
    /// the same quality as the compact ones, but sound far more robotic.
    var isLegacy: Bool { id.contains(".eloquence.") || id.contains(".speech.synthesis.") }

    /// English voices, best first: premium, then enhanced, then standard;
    /// within each, modern before legacy, then the phone's own accent.
    static func ranked(_ voices: [CookVoice], for language: String) -> [CookVoice] {
        voices.filter(\.isEnglish).sorted { a, b in
            if a.quality != b.quality { return a.quality > b.quality }
            if a.isLegacy != b.isLegacy { return !a.isLegacy }
            if (a.language == language) != (b.language == language) { return a.language == language }
            return a.name.localizedStandardCompare(b.name) == .orderedAscending
        }
    }

    struct Resolved: Equatable, Sendable {
        /// What steps are read in; nil only if the phone has no English voice.
        var voice: CookVoice?
        /// A voice was chosen but isn't on the phone (deleted, or iOS freed the
        /// space): the best installed one stands in until it's back.
        var chosenIsMissing = false
    }

    /// The chosen voice if it's installed, else the best one that is.
    /// `chosen` nil means automatic.
    static func resolve(chosen: String?, from installed: [CookVoice], language: String) -> Resolved {
        let ranked = ranked(installed, for: language)
        guard let chosen else { return Resolved(voice: ranked.first) }
        if let voice = ranked.first(where: { $0.id == chosen }) { return Resolved(voice: voice) }
        return Resolved(voice: ranked.first, chosenIsMissing: true)
    }

    /// Only standard voices installed: worth saying how to download a better one.
    static func needsDownload(_ installed: [CookVoice]) -> Bool {
        !installed.contains { $0.isEnglish && $0.quality > .standard }
    }

    /// The accent to prefer: the phone's, if it's English; otherwise British,
    /// like the rest of the app.
    static func language(forPhone code: String) -> String {
        code == "en" || code.hasPrefix("en-") ? code : "en-GB"
    }

    static let sample = "Melt half the butter in a large frying pan, then add the onion and cook for 5 minutes."
}

// MARK: - on this iPhone

extension CookVoice {
    /// Novelty voices (Bells, Bubbles…) can't read a recipe, and Personal
    /// Voice needs its own permission: neither is offered.
    init?(_ voice: AVSpeechSynthesisVoice) {
        guard !voice.voiceTraits.contains(.isNoveltyVoice),
              !voice.voiceTraits.contains(.isPersonalVoice) else { return nil }
        let quality: Quality = switch voice.quality {
        case .premium: .premium
        case .enhanced: .enhanced
        default: .standard
        }
        self.init(id: voice.identifier, name: voice.name, language: voice.language, quality: quality)
    }

    /// Read afresh each time: voices are downloaded and deleted in Settings
    /// while the app is running.
    static var installed: [CookVoice] {
        AVSpeechSynthesisVoice.speechVoices().compactMap(CookVoice.init)
    }

    static var phoneLanguage: String { language(forPhone: AVSpeechSynthesisVoice.currentLanguageCode()) }

    static func inUse(chosen: String?) -> Resolved {
        resolve(chosen: chosen, from: installed, language: phoneLanguage)
    }

    static func synthesisVoice(chosen: String?) -> AVSpeechSynthesisVoice? {
        inUse(chosen: chosen).voice.flatMap { AVSpeechSynthesisVoice(identifier: $0.id) }
            ?? AVSpeechSynthesisVoice(language: phoneLanguage)
    }
}
