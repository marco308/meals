import SwiftUI

/// Editing one ingredient line: what it is, how much, in what unit.
///
/// Shared by a meal's "on the side" list (#30/#31, where the amount used to be
/// a free-text guess and existing lines couldn't be touched at all) and the
/// recipe editor (#29). One component so a quantity is entered the same way
/// wherever it's entered.
struct IngredientLineEditor: View {
    @Environment(\.dismiss) private var dismiss

    let title: String
    let onSave: (LooseLine) -> Void

    @State private var name: String
    @State private var amount: String
    @State private var unit: String
    @State private var showCustomUnit = false
    @FocusState private var nameFocused: Bool

    init(line: LooseLine?, title: String, onSave: @escaping (LooseLine) -> Void) {
        self.title = title
        self.onSave = onSave
        _name = State(initialValue: line?.name ?? "")
        _amount = State(initialValue: line.flatMap { $0.quantity.map(Self.amountText) } ?? "")
        _unit = State(initialValue: line?.unit ?? "")
    }

    /// The conversion the API would refuse with, shown here instead — at the
    /// field, before the whole meal fails to save.
    private var unitWarning: String? {
        MealsUnits.rejection(for: unit).map { "\($0). The list only takes metric or a count." }
    }

    private var canSave: Bool {
        !name.trimmingCharacters(in: .whitespaces).isEmpty && unitWarning == nil
    }

    var body: some View {
        Form {
            Section("Ingredient") {
                TextField("e.g. frozen peas", text: $name)
                    .focused($nameFocused)
                    .textInputAutocapitalization(.never)
            }

            Section {
                HStack {
                    Text("Amount")
                    Spacer()
                    TextField("optional", text: $amount)
                        .keyboardType(.decimalPad)
                        .multilineTextAlignment(.trailing)
                        .frame(maxWidth: 120)
                        .monospacedDigit()
                }
                Picker("Unit", selection: unitSelection) {
                    Text("none").tag("")
                    ForEach(MealsUnits.common, id: \.self) { Text($0).tag($0) }
                    Text("other…").tag(Self.otherUnit)
                }
                // Only once "other…" is chosen: the vocabulary is a shortcut,
                // not a limit — a unit the server knows and this build doesn't
                // must still be typeable.
                if isCustomUnit {
                    TextField("Unit (tin, clove, pack…)", text: $unit)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
            } header: {
                Text("How much")
            } footer: {
                if let unitWarning {
                    Text(unitWarning).foregroundStyle(.red)
                } else {
                    Text("Leave the amount blank for things you don't measure. Metric (g, ml) or a count of a natural unit — 2 tins, 3 cloves.")
                }
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Done") { save() }.disabled(!canSave)
            }
        }
        .onAppear { nameFocused = name.isEmpty }
    }

    static let otherUnit = "__other__"

    private var isCustomUnit: Bool {
        showCustomUnit || (!unit.isEmpty && !MealsUnits.common.contains(unit))
    }

    private var unitSelection: Binding<String> {
        Binding(
            get: { isCustomUnit ? Self.otherUnit : unit },
            set: { picked in
                if picked == Self.otherUnit {
                    showCustomUnit = true
                } else {
                    showCustomUnit = false
                    unit = picked
                }
            }
        )
    }

    private func save() {
        let trimmedName = name.trimmingCharacters(in: .whitespaces)
        let trimmedUnit = unit.trimmingCharacters(in: .whitespaces)
        // A quantity with no unit would render as nothing at all, so a bare
        // number counts things (Q2's "4 items").
        let quantity = Double(amount.replacingOccurrences(of: ",", with: "."))
        onSave(
            LooseLine(
                name: trimmedName,
                quantity: quantity,
                unit: quantity == nil ? nil : (trimmedUnit.isEmpty ? "item" : trimmedUnit)
            )
        )
        dismiss()
    }

    static func amountText(_ value: Double) -> String {
        value == value.rounded() ? String(Int(value)) : String(value)
    }
}

/// "Showing a saved copy" — the honest version of what an offline read is.
/// The shopping list is the only screen that can promise changes will sync,
/// because it's the only one with a queue behind it (Q11 / #33).
struct OfflineBanner: View {
    let what: String

    var body: some View {
        Label("Offline — showing the last saved \(what)", systemImage: "wifi.slash")
            .font(.footnote)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
            .background(.bar)
    }
}

/// A line in an editable list: what it is and how much, tappable to change,
/// with a labelled delete rather than a bare hidden swipe (#31).
struct EditableLineRow: View {
    let line: LooseLine
    let onEdit: () -> Void
    let onDelete: () -> Void

    var body: some View {
        Button(action: onEdit) {
            HStack {
                Text(line.name).foregroundStyle(.primary)
                Spacer()
                Text(ShoppingListStore.displayQuantity(line.quantity, line.unit))
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
        .swipeActions(edge: .trailing) {
            Button("Remove", systemImage: "trash", role: .destructive, action: onDelete)
        }
    }
}
