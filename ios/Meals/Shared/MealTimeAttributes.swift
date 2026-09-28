import ActivityKit
import Foundation

/// A meal time on the Lock Screen: what the plan offers for breakfast, lunch or
/// dinner, for as long as that meal time lasts.
///
/// Compiled into both the app (which starts, updates and ends the activities)
/// and the widget extension (which draws them), so everything the extension
/// needs rides in here: it has no API client, no cache and no session.
struct MealTimeAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable, Sendable {
        /// Meal names still uncooked in the plan for this meal time, in plan
        /// order. Empty only when the user asked to see empty meal times.
        var options: [String]
    }

    /// "breakfast", "lunch" or "dinner": the slot the options were read from.
    let slot: String
    let startsAt: Date
    let endsAt: Date

    var title: String { slot.prefix(1).uppercased() + slot.dropFirst() }

    var symbol: String {
        switch slot {
        case "breakfast": "sunrise"
        case "lunch": "sun.max"
        default: "moon.stars"
        }
    }
}
