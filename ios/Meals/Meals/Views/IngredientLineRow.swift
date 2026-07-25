import SwiftUI

/// One ingredient line on a recipe or meal screen: aisle emoji, name, the
/// value-tier badge, the cupboard glyph when it's a staple, and the quantity.
/// Tapping opens the ingredient editor — which is also how a mis-flagged staple
/// gets fixed.
///
/// The staple marker matters here (issue #17): staples are filtered off the
/// shopping list until the pre-shop check, so without it there's no way to tell
/// from a recipe which lines you'll actually be buying. `cabinet` is the same
/// symbol the shopping list uses for staples, so the glyph means one thing
/// everywhere in the app.
struct IngredientLineRow: View {
    let line: RecipeLine
    var onIngredientChanged: () -> Void

    var body: some View {
        NavigationLink {
            IngredientEditorView(ingredientId: line.ingredientId, onChange: onIngredientChanged)
        } label: {
            HStack(spacing: 6) {
                Text(line.aisle)
                Text(line.name)
                if !line.tier.badge.isEmpty {
                    Text(line.tier.badge)
                        .font(.caption2)
                        .accessibilityLabel(line.tier.short)
                }
                if line.isStaple {
                    // Scales with the row's text rather than pinning a size:
                    // at .caption the cupboard is too small to read as one.
                    Image(systemName: "cabinet")
                        .imageScale(.small)
                        .foregroundStyle(.secondary)
                        .accessibilityLabel("Staple — kept in the cupboard, not on the shopping list")
                }
                Spacer()
                Text(line.display)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
        }
    }
}
