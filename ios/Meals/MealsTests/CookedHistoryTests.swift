import XCTest
@testable import Meals

/// The "cooked 12×" phrasing (issue #13). Timestamps stay strings app-wide, so
/// the month label is parsed from the ISO-8601 prefix.
final class CookedHistoryTests: XCTestCase {
    func testNeverCookedSaysNothing() {
        XCTAssertNil(CookedHistory.summary(times: 0, lastCookedAt: nil))
        XCTAssertNil(CookedHistory.summary(times: nil, lastCookedAt: nil))
    }

    func testCountAndMonth() {
        XCTAssertEqual(
            CookedHistory.summary(times: 12, lastCookedAt: "2026-05-03T18:30:00Z"),
            "cooked 12× · last May 2026"
        )
    }

    func testCountAloneWhenTheTimestampIsMissingOrJunk() {
        XCTAssertEqual(CookedHistory.summary(times: 1, lastCookedAt: nil), "cooked 1×")
        XCTAssertEqual(CookedHistory.summary(times: 1, lastCookedAt: "not-a-date"), "cooked 1×")
        XCTAssertEqual(CookedHistory.summary(times: 1, lastCookedAt: "2026-13-01T00:00:00Z"), "cooked 1×")
    }

    func testMonthLabelHandlesBothOffsetForms() {
        XCTAssertEqual(CookedHistory.monthLabel("2026-01-09T12:00:00+00:00"), "Jan 2026")
        XCTAssertEqual(CookedHistory.monthLabel("2026-12-31T23:59:59Z"), "Dec 2026")
        XCTAssertNil(CookedHistory.monthLabel("2026"))
        XCTAssertNil(CookedHistory.monthLabel(nil))
    }
}
