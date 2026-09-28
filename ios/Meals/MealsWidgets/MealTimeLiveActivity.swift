import ActivityKit
import SwiftUI
import WidgetKit

@main
struct MealsWidgets: WidgetBundle {
    var body: some Widget {
        MealTimeLiveActivity()
    }
}

/// A meal time on the Lock Screen and in the Dynamic Island: what the plan
/// offers, at a glance. Everything drawn comes from the activity itself; the
/// app keeps it current.
struct MealTimeLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: MealTimeAttributes.self) { context in
            LockScreenMealTime(attributes: context.attributes, options: context.state.options, over: context.isStale)
                .padding()
                .activityBackgroundTint(nil)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Label(context.attributes.title, systemImage: context.attributes.symbol)
                        .font(.headline)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text("until \(context.attributes.endsAt, style: .time)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    OptionList(options: context.state.options, limit: 3)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            } compactLeading: {
                Image(systemName: context.attributes.symbol)
            } compactTrailing: {
                Text(context.state.options.isEmpty ? "–" : "\(context.state.options.count)")
                    .monospacedDigit()
            } minimal: {
                Image(systemName: context.attributes.symbol)
            }
        }
    }
}

private struct LockScreenMealTime: View {
    let attributes: MealTimeAttributes
    let options: [String]
    let over: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label(attributes.title, systemImage: attributes.symbol)
                    .font(.headline)
                Spacer()
                if over {
                    Text("Over").font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("\(attributes.startsAt, style: .time) – \(attributes.endsAt, style: .time)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            OptionList(options: options, limit: 4)
        }
    }
}

private struct OptionList: View {
    let options: [String]
    let limit: Int

    var body: some View {
        if options.isEmpty {
            Text("Nothing planned").foregroundStyle(.secondary)
        } else {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(options.prefix(limit), id: \.self) { name in
                    Text("• \(name)").lineLimit(1)
                }
                if options.count > limit {
                    Text("and \(options.count - limit) more")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}
