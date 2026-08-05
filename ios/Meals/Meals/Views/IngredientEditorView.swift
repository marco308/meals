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
    @State private var showRename = false
    @State private var renameDraft = ""
    @State private var renameNotice: String?
    @State private var pendingRenameTarget: IngredientInfo?
    /// A rename hands the screen over to the surviving row — edits after it
    /// must land there, not on the id this editor was pushed with.
    @State private var currentId: UUID?
    @FocusState private var noteFocused: Bool

    private var activeId: UUID { currentId ?? ingredientId }

    var body: some View {
        List {
            if let info {
                Section {
                    // Tapping the name renames, like the web app — but through the
                    // fold-aware alert flow (see rename(to:)), never an in-place
                    // text field: the name is an identity key, not a plain field.
                    Button {
                        renameDraft = info.name
                        showRename = true
                    } label: {
                        HStack {
                            // Color.primary/.secondary, not the hierarchical
                            // styles — inside a list Button those resolve
                            // against the tint and the whole row goes green.
                            Text("Ingredient")
                                .foregroundStyle(Color.primary)
                            Spacer()
                            Text(info.name)
                                .foregroundStyle(Color.secondary)
                            Image(systemName: "pencil")
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(.tint)
                        }
                    }
                    .disabled(isSaving)
                    .accessibilityHint("Renames the ingredient everywhere it appears")
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
                        "To rename, tap the name at the top — every recipe and list line "
                            + "follows it, and if the new name already exists the two fold "
                            + "together. Merge is for duplicates spelled too differently for "
                            + "the finder — 'beef mince' next to 'minced beef'."
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
        .alert("Rename '\(info?.name ?? "")'", isPresented: $showRename) {
            TextField("Name", text: $renameDraft)
            Button("Cancel", role: .cancel) {}
            Button("Rename") {
                Task { await rename(to: renameDraft) }
            }
        } message: {
            Text("Every recipe, meal and shopping list line follows the ingredient to its new name.")
        }
        .alert(
            "Same ingredient",
            isPresented: .init(get: { renameNotice != nil }, set: { if !$0 { renameNotice = nil } })
        ) {
            Button("OK") { renameNotice = nil }
        } message: {
            Text(renameNotice ?? "")
        }
        .confirmationDialog(
            "'\(renameDraft.trimmingCharacters(in: .whitespaces))' already exists as '\(pendingRenameTarget?.name ?? "")'. Merge '\(info?.name ?? "")' into it? It keeps its own aisle, staple flag and verdict. This can't be undone.",
            isPresented: .init(get: { pendingRenameTarget != nil }, set: { if !$0 { pendingRenameTarget = nil } }),
            titleVisibility: .visible
        ) {
            Button("Merge them", role: .destructive) {
                guard let target = pendingRenameTarget else { return }
                pendingRenameTarget = nil
                Task { await adopt(target, absorbing: info) }
            }
        }
    }

    /// Rename, as sugar over create-and-merge (Q21): the name is the identity
    /// key every write finds-or-creates by, so a raw name edit would strand
    /// future writes on a fresh, uncurated row. Fold-aware instead: same row
    /// means nothing to rename, an existing row means merge (after asking),
    /// a free name means create it carrying this row's curation and fold.
    private func rename(to typed: String) async {
        guard let info else { return }
        let newName = typed.trimmingCharacters(in: .whitespaces)
        guard !newName.isEmpty, newName.lowercased() != info.name.lowercased() else { return }
        isSaving = true
        defer { isSaving = false }
        do {
            if let target = try await session.api.ingredient(named: newName) {
                if target.id == info.id {
                    renameNotice = "'\(newName)' is filed under '\(info.name)' — that's already this ingredient."
                } else {
                    pendingRenameTarget = target  // folding two foods together needs a yes
                }
                return
            }
            // The new name is free: create it carrying this row's curation — a
            // rename is a respelling, not a change of opinion — then fold the
            // old row into it.
            let keeper = try await session.api.createIngredient(
                name: newName, aisle: info.aisle, isStaple: info.isStaple,
                valueTier: info.tier, valueNote: info.valueNote
            )
            await adopt(keeper, absorbing: info)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Fold `absorbing` into `keeper` and hand this screen over to the
    /// survivor, so the title, fields and any further edits follow it.
    private func adopt(_ keeper: IngredientInfo, absorbing source: IngredientInfo?) async {
        guard let source else { return }
        isSaving = true
        defer { isSaving = false }
        do {
            let result = try await session.api.mergeIngredients(keeperId: keeper.id, duplicateIds: [source.id])
            currentId = result.ingredient.id
            info = result.ingredient
            noteDraft = result.ingredient.valueNote ?? ""
            await listStore.sync()  // names on the list just changed
            onChange?()
        } catch {
            errorMessage = error.localizedDescription
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
            let loaded = try await session.api.ingredient(id: activeId)
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
                    id: activeId, aisle: aisle, isStaple: isStaple, valueTier: valueTier, valueNote: valueNote
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
