import SwiftUI

/// Settings → Cook mode: which voice reads the steps, how fast, and whether
/// moving to a step reads it. Remembered on this iPhone only.
struct CookVoiceView: View {
    @Bindable private var preferences = CookPreferences.shared
    @State private var speaker = CookSpeaker()
    @State private var voices: [CookVoice] = []

    var body: some View {
        let resolved = CookVoice.resolve(chosen: preferences.voiceID, from: voices, language: CookVoice.phoneLanguage)
        List {
            Section {
                Toggle("Read each step", isOn: $preferences.readEachStep)
                Picker("Speed", selection: $preferences.rate) {
                    ForEach(CookPreferences.rates, id: \.self) { rate in
                        Text(rate == 1 ? "Normal" : "\(rate, format: .number)×").tag(rate)
                    }
                }
                Button("Play a sample", systemImage: "play.circle") { speaker.toggle(CookVoice.sample) }
            } footer: {
                Text("With Read each step on, moving to a step reads it out. Tapping a step always does.")
            }

            Section {
                row(title: "Automatic", detail: resolved.voice.map { "Now: \($0.name)" }, selected: preferences.voiceID == nil) {
                    preferences.voiceID = nil
                }
                ForEach(CookVoice.ranked(voices, for: CookVoice.phoneLanguage)) { voice in
                    row(title: voice.name, detail: "\(voice.quality.title) · \(voice.language)", selected: preferences.voiceID == voice.id) {
                        preferences.voiceID = voice.id
                        speaker.speak(CookVoice.sample)
                    }
                }
            } header: {
                Text("Voice")
            } footer: {
                if resolved.chosenIsMissing {
                    Text("The voice you chose isn't on this iPhone any more, so \(resolved.voice?.name ?? "the best one installed") is reading instead.")
                } else if CookVoice.needsDownload(voices) {
                    Text("Only basic voices are installed. For a far more natural one, download an Enhanced or Premium English voice in Settings › Accessibility › Spoken Content › Voices. They're free and work offline.")
                } else {
                    Text("More voices are in Settings › Accessibility › Spoken Content › Voices.")
                }
            }
        }
        .navigationTitle("Cook mode")
        .onAppear { voices = CookVoice.installed }
        .onDisappear { speaker.stop() }
    }

    private func row(title: String, detail: String?, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).foregroundStyle(.primary)
                    if let detail { Text(detail).font(.caption).foregroundStyle(.secondary) }
                }
                Spacer()
                if selected { Image(systemName: "checkmark").foregroundStyle(.tint) }
            }
        }
    }
}
