import Foundation

/// The quantity/unit convention (decision Q2) as the app needs to know it:
/// what to offer in a picker, and how to read an amount out of free text.
///
/// The vocabulary is a *convenience*, never a gate — `unit` stays a plain
/// String everywhere so a unit the server knows and this build doesn't can
/// still be typed, sent and displayed.
enum MealsUnits {
    /// Metric first (canonical g/ml, plus the kg/l the API converts for us),
    /// then the natural counts people actually shop in.
    static let common = ["g", "kg", "ml", "l", "item", "tin", "pack", "clove", "bunch", "slice", "jar", "bottle"]

    /// Units the API rejects, with the conversion it will quote back. Checked
    /// as the user types so the correction arrives at the field rather than
    /// after the whole meal or recipe fails to save, and before a quick add is
    /// queued: the offline queue drops whatever the server refuses (Q11).
    ///
    /// Every key of the backend's `BANNED_UNITS` (`app/services/units.py`),
    /// plurals included, each with the conversion quoted there. There is no
    /// iOS job in CI, so `backend/tests/unit/test_ios_units.py` keeps the two
    /// in step from the side that does run.
    static let rejected: [String: String] = [
        "tsp": "1 tsp = 5 ml",
        "teaspoon": "1 tsp = 5 ml",
        "teaspoons": "1 tsp = 5 ml",
        "tbsp": "1 tbsp = 15 ml",
        "tablespoon": "1 tbsp = 15 ml",
        "tablespoons": "1 tbsp = 15 ml",
        "cup": "1 cup = 240 ml",
        "cups": "1 cup = 240 ml",
        "oz": "1 oz = 28 g",
        "ounce": "1 oz = 28 g",
        "ounces": "1 oz = 28 g",
        "lb": "1 lb = 454 g",
        "lbs": "1 lb = 454 g",
        "pound": "1 lb = 454 g",
        "pounds": "1 lb = 454 g",
        "pint": "1 UK pint = 568 ml",
        "pints": "1 UK pint = 568 ml",
        "fl oz": "1 fl oz = 28 ml",
        "floz": "1 fl oz = 28 ml",
        "quart": "1 quart = 946 ml",
        "gallon": "1 gallon = 3785 ml",
        "stick": "1 stick of butter = 113 g",
        "sticks": "1 stick of butter = 113 g",
    ]

    /// nil when the unit is fine; otherwise the conversion to show.
    ///
    /// Past the banned list, the server takes any word at all as a natural
    /// unit, in any script, with spaces or hyphens inside it. What it refuses
    /// is anything else ("l.", "2kg"), so that is refused here too; a word
    /// this build has never heard of is still the server's business.
    static func rejection(for unit: String?) -> String? {
        guard let unit else { return nil }
        let cleaned = unit.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { return nil }
        if let conversion = rejected[cleaned] { return conversion }
        let word = cleaned.filter { $0 != " " && $0 != "-" }
        guard !word.isEmpty, word.allSatisfy(\.isLetter) else { return "“\(cleaned)” isn't a unit" }
        return nil
    }

    /// nil when the API will take the amount; otherwise what to say. Zero and
    /// negatives are refused there, and so is what `Double` will happily read
    /// out of "inf" or "nan": neither can even be written as JSON.
    static func rejection(forAmount quantity: Double?) -> String? {
        guard let quantity else { return nil }
        return quantity.isFinite && quantity > 0 ? nil : "An amount has to be a number above zero"
    }

    /// How an amount is written, in the app's own lists and in the editor's
    /// field: "2", "1.5", and "1e+20" for the absurd. Never `Int(value)` on its
    /// own, which traps past Int.max and on infinity. The server stores and
    /// serves amounts that large, so one such line would crash the Shopping
    /// tab on every phone in the household. Plain rather than localised, so
    /// the editor can read back what it wrote.
    static func amountText(_ value: Double) -> String {
        if let whole = Int(exactly: value) { return String(whole) }
        return String(value)
    }
}

/// Reads "200 g frozen peas" — and, since #30, "frozen peas 200g" — into its
/// parts. Used by the shopping list's quick add and by the meal editor's side
/// entry, which is why it lives here rather than in either view.
///
/// The glued form is the one people type off a packet, and the old parser
/// silently folded it into the *name*, minting a canonical ingredient called
/// "frozen peas 200g". Anything genuinely ambiguous still comes back as a bare
/// name — the explicit amount fields are the answer there, not more guessing.
enum QuantityParser {
    static func parse(_ text: String) -> (name: String, quantity: Double?, unit: String?) {
        let words = text.split(separator: " ").map(String.init)
        guard words.count >= 2 else { return (text, nil, nil) }

        // "frozen peas 200 g"
        if words.count >= 3, let quantity = Double(words[words.count - 2]) {
            return (words.dropLast(2).joined(separator: " "), quantity, words[words.count - 1])
        }
        // "200 g frozen peas"
        if words.count >= 3, let quantity = Double(words[0]) {
            return (words.dropFirst(2).joined(separator: " "), quantity, words[1])
        }
        // "frozen peas 200g"
        if let glued = splitGlued(words[words.count - 1]), words.count >= 2 {
            return (words.dropLast().joined(separator: " "), glued.0, glued.1)
        }
        // "200g frozen peas"
        if let glued = splitGlued(words[0]) {
            return (words.dropFirst().joined(separator: " "), glued.0, glued.1)
        }
        // "6 eggs" — a count of a thing, which is a natural unit (Q2). Without
        // this the whole string becomes an ingredient named "6 eggs".
        if words.count == 2, let quantity = Double(words[0]) {
            return (words[1], quantity, "item")
        }
        return (text, nil, nil)
    }

    /// "200g" → (200, "g"). nil for anything that isn't digits-then-letters.
    private static func splitGlued(_ word: String) -> (Double, String)? {
        let digits = word.prefix { $0.isNumber || $0 == "." }
        let rest = word.dropFirst(digits.count)
        guard !digits.isEmpty, !rest.isEmpty, rest.allSatisfy({ $0.isLetter }),
              let quantity = Double(digits)
        else { return nil }
        return (quantity, String(rest))
    }
}
