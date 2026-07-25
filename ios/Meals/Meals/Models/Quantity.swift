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
    /// after the whole meal or recipe fails to save.
    static let rejected: [String: String] = [
        "tsp": "1 tsp = 5 ml",
        "teaspoon": "1 tsp = 5 ml",
        "tbsp": "1 tbsp = 15 ml",
        "tablespoon": "1 tbsp = 15 ml",
        "cup": "1 cup = 240 ml",
        "cups": "1 cup = 240 ml",
        "oz": "1 oz = 28 g",
        "ounce": "1 oz = 28 g",
        "lb": "1 lb = 454 g",
        "pound": "1 lb = 454 g",
        "pint": "1 UK pint = 568 ml",
        "stick": "sticks aren't metric — use g or ml",
    ]

    /// nil when the unit is fine; otherwise the conversion to show.
    static func rejection(for unit: String?) -> String? {
        guard let unit, !unit.isEmpty else { return nil }
        return rejected[unit.lowercased().trimmingCharacters(in: .whitespaces)]
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
