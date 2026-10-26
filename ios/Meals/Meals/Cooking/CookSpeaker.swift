import AVFoundation
import Observation
import UIKit

/// Reads cook-mode steps aloud in the voice chosen in Settings, turning any
/// music down while it speaks rather than stopping it.
@MainActor
@Observable
final class CookSpeaker {
    private(set) var isSpeaking = false

    @ObservationIgnored private let synthesizer = AVSpeechSynthesizer()
    @ObservationIgnored private let events: SpeechEvents
    @ObservationIgnored private let preferences: CookPreferences

    init(preferences: CookPreferences = .shared) {
        self.preferences = preferences
        events = SpeechEvents()
        events.finished = { [weak self] in Task { @MainActor in self?.finished() } }
        synthesizer.delegate = events
    }

    func speak(_ text: String) {
        stop()
        guard !text.isEmpty else { return }
        let audio = AVAudioSession.sharedInstance()
        try? audio.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
        try? audio.setActive(true)
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = CookVoice.synthesisVoice(chosen: preferences.voiceID)
        utterance.rate = Float(Double(AVSpeechUtteranceDefaultSpeechRate) * preferences.rate)
        // Steps are read on their own, so the voice settles before starting.
        utterance.preUtteranceDelay = 0.05
        isSpeaking = true
        synthesizer.speak(utterance)
    }

    func stop() {
        guard synthesizer.isSpeaking else { return }
        synthesizer.stopSpeaking(at: .immediate)
    }

    func toggle(_ text: String) {
        if isSpeaking { stop() } else { speak(text) }
    }

    private func finished() {
        guard !synthesizer.isSpeaking else { return }
        isSpeaking = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}

/// Tells CookSpeaker when a line has finished or was cut off.
private final class SpeechEvents: NSObject, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    /// Set once, before the synthesizer can call back.
    var finished: @Sendable () -> Void = {}

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        finished()
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        finished()
    }
}
