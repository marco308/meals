import SwiftUI

/// The one place to see and edit an ingredient's metadata: canonical name,
/// supermarket aisle, staple flag, premium-vs-budget advice. Reached from any
/// ingredient row (recipe, meal, shopping item). Changes are saved immediately
/// and ripple everywhere the ingredient appears — one canonical ingredient per
/// name is the point.
struct IngredientEditorView: View {
    @Environment(Session.self) private var session
    @Environment(ShoppingListStore.self) private var listStore
    @Environment(\.dismiss) private var dismiss

    let ingredientId: UUID
    var onChange: (() -> Void)? = nil

    @State private var info: IngredientInfo?
    @State private var aisles: [Aisle] = []
    @State private var errorMessage: String?
    @State private var isSaving = false
    @State private var noteDraft = ""
    @State private var showMerge = false
    @FocusState private var noteFocused: Bool

    var body: some View {
        List {
            if let info {
                Section {
                    LabeledContent("Ingredient", value: info.name)
                    Toggle(isOn: stapleBinding) {
                        Label("Staple", systemImage: "cabinet")
                    }
                    .disabled(isSaving)
                } footer: {
                    Text("Staples stay off the shopping list until a staples check before shopping.")
                }

                Section {
                    Picker("Buying advice", selection: tierBinding) {
                        ForEach(ValueTier.allCases) { tier in
                            Text(tier.badge.isEmpty ? tier.short : "\(tier.badge) \(tier.short)").tag(tier)
                        }
                    }
                    .pickerStyle(.segmented)
                    .disabled(isSaving)

                    TextField("Why? e.g. the cheap stuff goes bitter", text: $noteDraft)
                        .focused($noteFocused)
                        .submitLabel(.done)
                        .disabled(isSaving)
                        .onSubmit { saveNoteIfChanged() }
                        .onChange(of: noteFocused) { _, focused in
                            if !focused { saveNoteIfChanged() }
                        }
                } header: {
                    Text("Premium or budget?")
                } footer: {
                    Text("Decide once; the verdict shows next to the item on the shopping list, when you're at the shelf.")
                }

                Section("Aisle") {
                    ForEach(aisles, id: \.emoji) { aisle in
                        Button {
                            save(aisle: aisle.emoji)
                        } label: {
                            HStack {
                                Text("\(aisle.emoji)  \(aisle.label)")
                                    .foregroundStyle(.primary)
                                Spacer()
                                if info.aisle == aisle.emoji {
                                    Image(systemName: "checkmark").foregroundStyle(.tint)
                                }
                            }
                        }
                        .disabled(isSaving)
                    }
                }

                Section {
                    Button {
                        showMerge = true
                    } label: {
                        Label("Merge into another ingredient…", systemImage: "arrow.triangle.merge")
                    }
                    .disabled(isSaving)
                } footer: {
                    Text(
                        "For duplicates spelled too differently for the finder — 'beef mince' "
                            + "next to 'minced beef'. Everything using '\(info.name)' moves onto "
                            + "the ingredient you pick, then '\(info.name)' is deleted."
                    )
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.red).font(.callout)
                    }
                }
            } else if let errorMessage {
                ContentUnavailableView(
                    "Couldn't load ingredient", systemImage: "exclamationmark.triangle",
                    description: Text(errorMessage)
                )
            } else {
                ProgressView()
            }
        }
        .navigationTitle(info?.name.capitalized ?? "Ingredient")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .sheet(isPresented: $showMerge) {
            if let info {
                MergeIntoSheet(source: info) {
                    // This ingredient no longer exists: re-sort the list,
                    // tell the presenter, and get off its screen.
                    Task { await listStore.sync() }
                    onChange?()
                    dismiss()
                }
            }
        }
    }

    private var stapleBinding: Binding<Bool> {
        Binding(
            get: { info?.isStaple ?? false },
            set: { value in save(isStaple: value) }
        )
    }

    private var tierBinding: Binding<ValueTier> {
        Binding(
            get: { info?.tier ?? .any },
            set: { value in save(valueTier: value) }
        )
    }

    private func load() async {
        do {
            let loaded = try await session.api.ingredient(id: ingredientId)
            info = loaded
            noteDraft = loaded.valueNote ?? ""
            aisles = try await session.api.fetchAisles()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// The note saves on Done or when the field loses focus — an empty string
    /// clears it server-side.
    private func saveNoteIfChanged() {
        let trimmed = noteDraft.trimmingCharacters(in: .whitespaces)
        guard trimmed != (info?.valueNote ?? "") else { return }
        save(valueNote: trimmed)
    }

    private func save(
        aisle: String? = nil, isStaple: Bool? = nil, valueTier: ValueTier? = nil, valueNote: String? = nil
    ) {
        isSaving = true
        errorMessage = nil
        Task {
            defer { isSaving = false }
            do {
                let updated = try await session.api.updateIngredient(
                    id: ingredientId, aisle: aisle, isStaple: isStaple, valueTier: valueTier, valueNote: valueNote
                )
                info = updated
                noteDraft = updated.valueNote ?? ""
                await listStore.sync()  // aisle/staple/value changes re-sort, re-filter and re-badge the list
                onChange?()
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
