import SwiftUI

/// When breakfast, lunch and dinner are, for the Lock Screen: during each one
/// it shows what the plan offers for it.
struct MealTimesView: View {
    @Environment(MealTimes.self) private var mealTimes

    var body: some View {
        @Bindable var mealTimes = mealTimes
        Form {
            Section {
                Toggle("Show meal times", isOn: $mealTimes.settings.enabled)
            } footer: {
                Text(
                    "During each meal time, the Lock Screen and Dynamic Island show the meals in your "
                        + "plan for it that haven't been cooked yet."
                )
            }

            if mealTimes.settings.enabled {
                ForEach(MealTime.allCases) { time in
                    MealTimeSection(time: time, window: $mealTimes.settings[time])
                }

                Section {
                    Toggle("Show when nothing's planned", isOn: $mealTimes.settings.showWhenEmpty)
                } footer: {
                    Text(
                        "Off, a meal time with nothing in the plan for it doesn't appear at all. "
                            + "On, it appears anyway and says nothing's planned."
                    )
                }
            }

            if !mealTimes.systemAllowsActivities {
                Section {
                    Text("Live Activities are turned off for Meals. Turn them on in iOS Settings → Meals.")
                        .foregroundStyle(.secondary)
                }
            } else if !MealTimes.canScheduleAhead {
                Section {
                    Text(
                        "On this version of iOS a meal time appears when you open the app during it. "
                            + "From iOS 26 it appears by itself."
                    )
                    .foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle("Meal times")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct MealTimeSection: View {
    let time: MealTime
    @Binding var window: MealTimeWindow

    var body: some View {
        Section(time.label) {
            Toggle("On", isOn: $window.enabled)
            if window.enabled {
                DatePicker("From", selection: minutes(\.start), in: ...latestStart, displayedComponents: .hourAndMinute)
                DatePicker("Until", selection: minutes(\.end), in: earliestEnd..., displayedComponents: .hourAndMinute)
            }
        }
    }

    /// A time of day as today's date, which is what DatePicker wants; only the
    /// hour and minute are kept.
    private func minutes(_ key: WritableKeyPath<MealTimeWindow, Int>) -> Binding<Date> {
        Binding {
            Self.date(minutes: window[keyPath: key])
        } set: { date in
            let parts = Calendar.current.dateComponents([.hour, .minute], from: date)
            window[keyPath: key] = (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
        }
    }

    /// A meal time ends after it starts, on the same day.
    private var latestStart: Date { Self.date(minutes: max(window.end - 1, 0)) }
    private var earliestEnd: Date { Self.date(minutes: min(window.start + 1, 24 * 60 - 1)) }

    private static func date(minutes: Int) -> Date {
        Calendar.current.date(
            bySettingHour: minutes / 60, minute: minutes % 60, second: 0, of: .now
        ) ?? .now
    }
}
