import SwiftUI
import UIKit

/// Cook mode (Q29): one step at a time, for following at the hob. Swipe or
/// tap Next to move, tap the step to hear it. The ingredients the step uses
/// are pinned under it with their amounts, and the rest sit below, ticked once
/// an earlier step has used them. Nothing re-sorts, so nothing jumps about.
///
/// The screen stays on while it is open: hands are floury and Face ID can't
/// see you from the hob.
struct CookModeView: View {
    @Environment(Session.self) private var session
    @Environment(RecipeStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State var recipe: Recipe
    @State private var current = 0
    @State private var speaker = CookSpeaker()
    @State private var assistant: AssistantState?
    @State private var showSteps = false
    @State private var askError: String?
    @State private var waitingForAssistant = false
    @Bindable private var preferences = CookPreferences.shared

    private var steps: [RecipeStep] { recipe.cookSteps }

    var body: some View {
        NavigationStack {
            TabView(selection: $current) {
                ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                    page(step, index: index).tag(index)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .safeAreaInset(edge: .bottom) { controls }
            .navigationTitle(steps.isEmpty ? recipe.title : "Step \(current + 1) of \(steps.count)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("All steps", systemImage: "list.number") { showSteps = true }
                        Toggle("Read each step", systemImage: "speaker.wave.2", isOn: $preferences.readEachStep)
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
            .sheet(isPresented: $showSteps) { stepList }
            .alert("Couldn't ask your assistant", isPresented: .init(
                get: { askError != nil }, set: { if !$0 { askError = nil } }
            )) {
                Button("OK") { askError = nil }
            } message: {
                Text(askError ?? "")
            }
        }
        .onAppear {
            UIApplication.shared.isIdleTimerDisabled = true
            if preferences.readEachStep { readCurrent() }
        }
        .onDisappear {
            UIApplication.shared.isIdleTimerDisabled = false
            speaker.stop()
        }
        .onChange(of: current) {
            speaker.stop()
            if preferences.readEachStep { readCurrent() }
        }
        .task {
            assistant = try? await session.api.assistant()
            if recentlyAsked { await waitForAssistant() }
        }
    }

    // MARK: - a step

    private func page(_ step: RecipeStep, index: Int) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Button {
                    speaker.toggle(step.spoken)
                } label: {
                    VStack(alignment: .leading, spacing: 8) {
                        if let section = step.section {
                            Text(section.uppercased())
                                .font(.caption.weight(.heavy))
                                .foregroundStyle(.orange)
                        }
                        Text(step.text)
                            .font(.title2.weight(.semibold))
                            .multilineTextAlignment(.leading)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .buttonStyle(.plain)
                .accessibilityHint("Reads the step aloud")

                pinned(step)
                rest(step, index: index)
                assistantNote
            }
            .padding()
        }
    }

    private func pinned(_ step: RecipeStep) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("For this step").font(.headline)
            if step.ingredients.isEmpty {
                Text("Nothing new from the ingredients list.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(step.ingredients) { ingredient in
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text(ingredient.amount ?? "")
                            .font(.body.monospacedDigit().weight(.semibold))
                            .foregroundStyle(.green)
                            .frame(minWidth: 72, alignment: .trailing)
                        Text(ingredient.name)
                            .font(.title3.weight(.medium))
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(.green.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(.green.opacity(0.6), lineWidth: 1.5))
    }

    /// The recipe's other lines, ticked when an earlier step has used them.
    private func rest(_ step: RecipeStep, index: Int) -> some View {
        let here = Set(step.ingredients.map(\.ingredientId))
        let used = Set(steps.prefix(index).flatMap { $0.ingredients.map(\.ingredientId) })
        var seen = Set<UUID>()
        let others = recipe.ingredients.filter { line in
            guard !here.contains(line.ingredientId), !seen.contains(line.ingredientId) else { return false }
            seen.insert(line.ingredientId)
            return true
        }
        return VStack(alignment: .leading, spacing: 6) {
            if !others.isEmpty {
                Text("Everything else").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
                ForEach(others) { line in
                    let done = used.contains(line.ingredientId)
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text(line.display)
                            .font(.footnote.monospacedDigit())
                            .frame(minWidth: 72, alignment: .trailing)
                        Text(line.name)
                        if done { Image(systemName: "checkmark").font(.caption) }
                    }
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .strikethrough(done)
                    .opacity(done ? 0.6 : 1)
                }
            }
        }
    }

    // MARK: - the assistant

    private var recentlyAsked: Bool {
        guard !recipe.stepsWrittenByAssistant,
              let asked = recipe.stepsRequestedAt.flatMap(Self.serverDate) else { return false }
        return Date().timeIntervalSince(asked) < 180
    }

    /// The API's datetimes are naive UTC ("2026-10-25T16:25:12.123456"); the
    /// seconds are all that matter here.
    static func serverDate(_ timestamp: String) -> Date? {
        guard timestamp.count >= 19 else { return nil }
        return try? Date(String(timestamp.prefix(19)) + "Z", strategy: .iso8601)
    }

    @ViewBuilder private var assistantNote: some View {
        if recipe.stepsWrittenByAssistant {
            Label("Steps written by your assistant", systemImage: "sparkles")
                .font(.footnote)
                .foregroundStyle(.secondary)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Text(recipe.stepsOutdated == true
                    ? "The recipe has changed since your assistant wrote its steps, so these are matched by name for now."
                    : "These steps were matched by name, so an amount split between steps shows in full on the first one.")
                if waitingForAssistant {
                    Label("Asked your assistant; this updates when it answers.", systemImage: "hourglass")
                } else if assistant?.configured == true {
                    Button("Ask my assistant", systemImage: "sparkles") { Task { await ask() } }
                        .buttonStyle(.bordered)
                } else {
                    Text("An AI assistant can write them with split amounts: set one up in the web app's Settings.")
                }
            }
            .font(.footnote)
            .foregroundStyle(.secondary)
        }
    }

    private func ask() async {
        do {
            recipe = try await session.api.askAssistantForSteps(recipeId: recipe.id)
            await waitForAssistant()
        } catch {
            askError = error.localizedDescription
        }
    }

    /// Checks every ten seconds for three minutes, while the screen is open.
    private func waitForAssistant() async {
        waitingForAssistant = true
        defer { waitingForAssistant = false }
        for _ in 0..<18 {
            try? await Task.sleep(for: .seconds(10))
            if Task.isCancelled { return }
            guard let fresh = try? await store.detail(id: recipe.id) else { continue }
            if fresh.stepsWrittenByAssistant {
                recipe = fresh
                current = min(current, max(fresh.cookSteps.count - 1, 0))
                return
            }
        }
    }

    // MARK: - controls

    private var controls: some View {
        HStack(spacing: 12) {
            Button {
                current -= 1
            } label: {
                Label("Back", systemImage: "chevron.left").frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .disabled(current == 0)

            Button {
                readCurrent(toggle: true)
            } label: {
                Image(systemName: speaker.isSpeaking ? "stop.fill" : "speaker.wave.2.fill")
                    .frame(minWidth: 44)
            }
            .buttonStyle(.borderedProminent)
            .tint(.green)
            .accessibilityLabel(speaker.isSpeaking ? "Stop reading" : "Read the step aloud")

            Button {
                current += 1
            } label: {
                Label("Next", systemImage: "chevron.right").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(current >= steps.count - 1)
        }
        .controlSize(.large)
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.bar)
    }

    private func readCurrent(toggle: Bool = false) {
        guard steps.indices.contains(current) else { return }
        let text = steps[current].spoken
        if toggle { speaker.toggle(text) } else { speaker.speak(text) }
    }

    private var stepList: some View {
        NavigationStack {
            List(Array(steps.enumerated()), id: \.offset) { index, step in
                Button {
                    current = index
                    showSteps = false
                } label: {
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text("\(step.number)").font(.headline).foregroundStyle(.orange)
                        VStack(alignment: .leading, spacing: 2) {
                            if let section = step.section, section != steps[safe: index - 1]?.section {
                                Text(section).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                            }
                            Text(step.text)
                        }
                    }
                    .foregroundStyle(index < current ? .secondary : .primary)
                    .fontWeight(index == current ? .semibold : .regular)
                }
            }
            .navigationTitle("Steps")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { showSteps = false }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}

private extension Array {
    subscript(safe index: Int) -> Element? { indices.contains(index) ? self[index] : nil }
}
