import XCTest
@testable import Meals

/// Which meal times reach the Lock Screen, when, and with what on offer.
final class MealTimesTests: XCTestCase {
    private var calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/London")!
        return calendar
    }()

    private func at(_ hour: Int, _ minute: Int = 0, day: Int = 28) -> Date {
        calendar.date(from: DateComponents(year: 2026, month: 9, day: day, hour: hour, minute: minute))!
    }

    private func planMeal(_ name: String, slots: [String], cooked: Bool = false) -> PlanMeal {
        PlanMeal(
            id: UUID(),
            meal: Meal(id: UUID(), name: name, slot: slots.first, recipes: [], looseIngredients: [], slots: slots),
            cookedAt: cooked ? "2026-09-27T18:00:00Z" : nil
        )
    }

    private func plan(_ meals: [PlanMeal]) -> Plan {
        Plan(id: UUID(), label: "This week", status: "active", meals: meals)
    }

    func testDefaultsAreTheAgreedHours() {
        let settings = MealTimeSettings()
        XCTAssertTrue(settings.enabled)
        XCTAssertFalse(settings.showWhenEmpty)
        XCTAssertEqual(settings.breakfast, .hours(7, 0, to: 8, 0))
        XCTAssertEqual(settings.lunch, .hours(12, 0, to: 13, 0))
        XCTAssertEqual(settings.dinner, .hours(17, 30, to: 18, 30))
    }

    func testOptionsAreUncookedMealsForThatSlotOnceEach() {
        let plan = plan([
            planMeal("Porridge", slots: ["breakfast"]),
            planMeal("Soup", slots: ["lunch", "dinner"]),
            planMeal("Chilli", slots: ["dinner"], cooked: true),
            planMeal("Soup", slots: ["dinner"]),
            planMeal("Curry", slots: ["Dinner"]),
            planMeal("Crisps", slots: []),
        ])
        XCTAssertEqual(MealTimePlanner.options(for: .breakfast, in: plan), ["Porridge"])
        XCTAssertEqual(MealTimePlanner.options(for: .lunch, in: plan), ["Soup"])
        XCTAssertEqual(MealTimePlanner.options(for: .dinner, in: plan), ["Soup", "Curry"])
        XCTAssertEqual(MealTimePlanner.options(for: .dinner, in: nil), [])
    }

    func testOccurrenceIsTodaysUntilItEndsThenTomorrows() {
        let dinner = MealTimeWindow.hours(17, 30, to: 18, 30)
        let before = MealTimePlanner.occurrence(of: dinner, after: at(9), calendar: calendar)
        XCTAssertEqual(before?.start, at(17, 30))
        XCTAssertEqual(before?.end, at(18, 30))

        let during = MealTimePlanner.occurrence(of: dinner, after: at(18), calendar: calendar)
        XCTAssertEqual(during?.start, at(17, 30))

        let after = MealTimePlanner.occurrence(of: dinner, after: at(18, 30), calendar: calendar)
        XCTAssertEqual(after?.start, at(17, 30, day: 29))
        XCTAssertEqual(after?.end, at(18, 30, day: 29))
    }

    func testAWindowThatEndsBeforeItStartsNeverShows() {
        XCTAssertNil(MealTimePlanner.occurrence(of: .hours(9, 0, to: 8, 0), after: at(6), calendar: calendar))
    }

    func testNothingPlannedStaysQuietUnlessAskedFor() {
        let plan = plan([planMeal("Porridge", slots: ["breakfast"])])
        var settings = MealTimeSettings()
        let quiet = MealTimePlanner.planned(settings: settings, plan: plan, now: at(6), calendar: calendar)
        XCTAssertEqual(quiet.map(\.time), [.breakfast])
        XCTAssertEqual(quiet.first?.options, ["Porridge"])

        settings.showWhenEmpty = true
        let all = MealTimePlanner.planned(settings: settings, plan: plan, now: at(6), calendar: calendar)
        XCTAssertEqual(all.map(\.time), [.breakfast, .lunch, .dinner])
        XCTAssertEqual(all.last?.options, [])
    }

    func testTurnedOffMealTimesAndTheMasterSwitch() {
        let plan = plan([planMeal("Porridge", slots: ["breakfast"]), planMeal("Soup", slots: ["lunch"])])
        var settings = MealTimeSettings()
        settings.breakfast.enabled = false
        XCTAssertEqual(
            MealTimePlanner.planned(settings: settings, plan: plan, now: at(6), calendar: calendar).map(\.time),
            [.lunch]
        )
        settings.enabled = false
        XCTAssertTrue(MealTimePlanner.planned(settings: settings, plan: plan, now: at(6), calendar: calendar).isEmpty)
    }

    func testSentence() {
        XCTAssertEqual(MealTimePlanner.sentence([]), "Nothing planned")
        XCTAssertEqual(MealTimePlanner.sentence(["Porridge"]), "Porridge")
        XCTAssertEqual(MealTimePlanner.sentence(["Porridge", "Eggs", "Toast"]), "Porridge, Eggs or Toast")
    }

    func testOlderSettingsKeepTheirDefaultsForMissingKeys() throws {
        let decoded = try JSONDecoder().decode(MealTimeSettings.self, from: Data(#"{"showWhenEmpty":true}"#.utf8))
        XCTAssertTrue(decoded.showWhenEmpty)
        XCTAssertEqual(decoded.dinner, .hours(17, 30, to: 18, 30))
    }
}
