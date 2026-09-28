import ActivityKit
import BackgroundTasks
import Foundation
import Observation

/// The three meal times the Lock Screen knows about. A local setting, never
/// decoded from the server, so a Swift enum is safe here; the slot names it
/// reads from meals are the server's own suggested ones.
enum MealTime: String, CaseIterable, Codable, Identifiable, Sendable {
    case breakfast
    case lunch
    case dinner

    var id: String { rawValue }
    var label: String { rawValue.capitalizedFirst }
}

/// One meal time's hours, as minutes past midnight so a setting doesn't drift
/// when the clocks change.
struct MealTimeWindow: Codable, Equatable, Sendable {
    var enabled: Bool
    var start: Int
    var end: Int

    static func hours(_ startHour: Int, _ startMinute: Int, to endHour: Int, _ endMinute: Int) -> MealTimeWindow {
        MealTimeWindow(enabled: true, start: startHour * 60 + startMinute, end: endHour * 60 + endMinute)
    }
}

struct MealTimeSettings: Codable, Equatable, Sendable {
    /// The whole feature, on or off.
    var enabled = true
    /// Off by default: a meal time with nothing planned for it stays quiet.
    var showWhenEmpty = false
    var breakfast = MealTimeWindow.hours(7, 0, to: 8, 0)
    var lunch = MealTimeWindow.hours(12, 0, to: 13, 0)
    var dinner = MealTimeWindow.hours(17, 30, to: 18, 30)

    subscript(time: MealTime) -> MealTimeWindow {
        get {
            switch time {
            case .breakfast: breakfast
            case .lunch: lunch
            case .dinner: dinner
            }
        }
        set {
            switch time {
            case .breakfast: breakfast = newValue
            case .lunch: lunch = newValue
            case .dinner: dinner = newValue
            }
        }
    }

    init() {}

    /// Tolerant of a settings blob written by an older build: anything missing
    /// keeps its default rather than resetting the lot.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let defaults = MealTimeSettings()
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled) ?? defaults.enabled
        showWhenEmpty = try c.decodeIfPresent(Bool.self, forKey: .showWhenEmpty) ?? defaults.showWhenEmpty
        breakfast = try c.decodeIfPresent(MealTimeWindow.self, forKey: .breakfast) ?? defaults.breakfast
        lunch = try c.decodeIfPresent(MealTimeWindow.self, forKey: .lunch) ?? defaults.lunch
        dinner = try c.decodeIfPresent(MealTimeWindow.self, forKey: .dinner) ?? defaults.dinner
    }
}

/// One meal time that should be on the Lock Screen: when, and what is on offer.
struct PlannedMealTime: Equatable, Sendable {
    let time: MealTime
    let start: Date
    let end: Date
    let options: [String]
}

/// The pure part: which meal times to show, and with what. Kept free of
/// ActivityKit so it can be tested.
enum MealTimePlanner {
    /// What the plan offers for a meal time: its uncooked meals that fill that
    /// slot, in plan order, each name once. A cooked meal has been eaten, so it
    /// is no longer an option.
    static func options(for time: MealTime, in plan: Plan?) -> [String] {
        var seen = Set<String>()
        return (plan?.meals ?? [])
            .filter { $0.cookedAt == nil && $0.meal.allSlots.contains { $0.lowercased() == time.rawValue } }
            .map(\.meal.name)
            .filter { seen.insert($0).inserted }
    }

    /// The meal time still to come or under way: today's if it hasn't ended,
    /// otherwise tomorrow's. nil for a window that doesn't make sense.
    static func occurrence(of window: MealTimeWindow, after now: Date, calendar: Calendar) -> (start: Date, end: Date)? {
        guard window.end > window.start else { return nil }
        for dayOffset in 0...1 {
            guard let day = calendar.date(byAdding: .day, value: dayOffset, to: calendar.startOfDay(for: now)),
                let start = calendar.date(
                    bySettingHour: window.start / 60, minute: window.start % 60, second: 0, of: day),
                let end = calendar.date(bySettingHour: window.end / 60, minute: window.end % 60, second: 0, of: day)
            else { return nil }
            if end > now { return (start, end) }
        }
        return nil
    }

    static func planned(
        settings: MealTimeSettings, plan: Plan?, now: Date, calendar: Calendar = .current
    ) -> [PlannedMealTime] {
        guard settings.enabled else { return [] }
        return MealTime.allCases.compactMap { time in
            let window = settings[time]
            guard window.enabled, let when = occurrence(of: window, after: now, calendar: calendar) else { return nil }
            let options = options(for: time, in: plan)
            guard !options.isEmpty || settings.showWhenEmpty else { return nil }
            return PlannedMealTime(time: time, start: when.start, end: when.end, options: options)
        }
    }

    /// "Porridge, eggs or toast" for the alert that opens a meal time.
    static func sentence(_ options: [String]) -> String {
        guard let last = options.last else { return "Nothing planned" }
        guard options.count > 1 else { return last }
        return options.dropLast().joined(separator: ", ") + " or " + last
    }
}

/// Puts each meal time on the Lock Screen as a Live Activity and keeps it true
/// to the plan.
///
/// iOS 26 can start an activity at a future time, so the next occurrence of
/// every meal time is scheduled ahead and appears without the app being open.
/// Older systems can only start one while the app is in front, so there a meal
/// time shows up when the app is opened during it.
///
/// Nothing ends an activity at the end of its window unless the app runs, so
/// every activity's content goes stale at that moment (the extension draws
/// that as over) and a background refresh is asked for then to take it down.
@MainActor
@Observable
final class MealTimes {
    static let refreshTaskIdentifier = "com.marcuslab.meals.mealtimes"
    private static let settingsKey = "mealTimeSettings"

    var settings: MealTimeSettings {
        didSet {
            guard settings != oldValue else { return }
            if let data = try? JSONEncoder().encode(settings) { defaults.set(data, forKey: Self.settingsKey) }
            Task { await refresh() }
        }
    }

    /// Whether the system lets this app show Live Activities at all (the user
    /// can turn them off per app in iOS Settings).
    var systemAllowsActivities: Bool { ActivityAuthorizationInfo().areActivitiesEnabled }

    /// Whether meal times can appear with the app closed.
    static var canScheduleAhead: Bool {
        if #available(iOS 26.0, *) { true } else { false }
    }

    private let defaults: UserDefaults
    private let plan: @MainActor () -> Plan?
    private let signedIn: @MainActor () -> Bool
    private var refreshing = false
    private var refreshAgain = false

    init(
        defaults: UserDefaults = .standard,
        plan: @escaping @MainActor () -> Plan?,
        signedIn: @escaping @MainActor () -> Bool
    ) {
        self.defaults = defaults
        self.plan = plan
        self.signedIn = signedIn
        if let data = defaults.data(forKey: Self.settingsKey),
            let saved = try? JSONDecoder().decode(MealTimeSettings.self, from: data)
        {
            settings = saved
        } else {
            settings = MealTimeSettings()
        }
    }

    /// Bring the Lock Screen in line with the plan and the settings. Calls that
    /// arrive while one is running fold into one more pass, so two quick plan
    /// changes can't both request the same meal time.
    func refresh(now: Date = .now) async {
        if refreshing {
            refreshAgain = true
            return
        }
        refreshing = true
        repeat {
            refreshAgain = false
            await reconcile(now: now)
        } while refreshAgain
        refreshing = false
        scheduleBackgroundRefresh(now: now)
    }

    private func reconcile(now: Date) async {
        let wanted = signedIn() ? MealTimePlanner.planned(settings: settings, plan: plan(), now: now) : []
        var kept = Set<MealTime>()

        for activity in Activity<MealTimeAttributes>.activities {
            let attributes = activity.attributes
            let match = wanted.first {
                $0.time.rawValue == attributes.slot && $0.start == attributes.startsAt && $0.end == attributes.endsAt
            }
            switch activity.activityState {
            case .ended, .dismissed:
                continue
            default:
                break
            }
            guard let match, !kept.contains(match.time) else {
                await activity.end(nil, dismissalPolicy: .immediate)
                continue
            }
            if activity.content.state.options == match.options {
                kept.insert(match.time)
            } else if activity.activityState == .active {
                await activity.update(Self.content(for: match))
                kept.insert(match.time)
            } else {
                // Scheduled but not started: replace it rather than trust an
                // update to reach something that isn't showing yet.
                await activity.end(nil, dismissalPolicy: .immediate)
            }
        }

        guard systemAllowsActivities else { return }
        for meal in wanted where !kept.contains(meal.time) {
            request(meal, now: now)
        }
    }

    private func request(_ meal: PlannedMealTime, now: Date) {
        let attributes = MealTimeAttributes(slot: meal.time.rawValue, startsAt: meal.start, endsAt: meal.end)
        let content = Self.content(for: meal)
        do {
            if meal.start <= now {
                _ = try Activity.request(attributes: attributes, content: content)
            } else if #available(iOS 26.0, *) {
                let alert = AlertConfiguration(
                    title: "\(meal.time.label)",
                    body: "\(MealTimePlanner.sentence(meal.options))",
                    sound: .default
                )
                _ = try Activity.request(
                    attributes: attributes, content: content, style: .standard,
                    alertConfiguration: alert, start: meal.start
                )
            }
        } catch {
            // Refused (turned off, or started from the background on a system
            // that can't): the next foreground tries again.
        }
    }

    private static func content(for meal: PlannedMealTime) -> ActivityContent<MealTimeAttributes.ContentState> {
        ActivityContent(state: .init(options: meal.options), staleDate: meal.end)
    }

    /// Ask to run again at the next moment something changes: the end of a
    /// meal time under way (to take it down) or the start of the next one (to
    /// roll the schedule a day forward). iOS decides when it actually runs.
    private func scheduleBackgroundRefresh(now: Date) {
        let all = MealTimePlanner.planned(
            settings: { var s = settings; s.showWhenEmpty = true; return s }(), plan: nil, now: now)
        guard let next = all.flatMap({ [$0.start, $0.end] }).filter({ $0 > now }).min() else {
            BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: Self.refreshTaskIdentifier)
            return
        }
        let request = BGAppRefreshTaskRequest(identifier: Self.refreshTaskIdentifier)
        request.earliestBeginDate = next
        try? BGTaskScheduler.shared.submit(request)
    }
}
