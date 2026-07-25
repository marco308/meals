import SwiftUI

/// Correcting a recipe in the app (issue #29).
///
/// Ingested recipes come from whatever JSON-LD the site published, and it is
/// often wrong in small ways — 4 servings when the page said 2, prep notes
/// stuck in an ingredient name, instructions full of site boilerplate. Until
/// now the only fixes were `curl` or delete-and-re-ingest, and re-ingesting
/// deliberately returns the cached recipe rather than re-parsing (Q3), so the
/// error was permanent.
///
/// `ingredients` is only sent when a line actually changed: that flag is what
/// makes the server re-sync every meal using the recipe, and a resync on every
/// title tweak is wasted work.
struct RecipeEditorView: View {
    @Environment(RecipeStore.self) private var store
    @Environment(ShoppingListStore.self) private var listStore
    @Environment(\.dismiss) private var dismiss

    let recipe: Recipe
    let onSaved: (Recipe) -> Void

    @State private var title: String
    @State private var servings: String
    @State private var prepMinutes: String
    @State private var cookMinutes: String
    @State private var tags: String
    @State private var imageUrl: String
    @State private var instructions: String
    @State private var lines: [LooseLine]
    @State private var editingLine: LooseLine?
    @State private var addingLine = false
    @State private var isSaving = false
    @State private var errorMessage: String?

    init(recipe: Recipe, onSaved: @escaping (Recipe) -> Void) {
        self.recipe = recipe
        self.onSaved = onSaved
        _title = State(initialValue: recipe.title)
        _servings = State(initialValue: recipe.servings.map(String.init) ?? "")
        _prepMinutes = State(initialValue: recipe.prepMinutes.map(String.init) ?? "")
        _cookMinutes = State(initialValue: recipe.cookMinutes.map(String.init) ?? "")
        _tags = State(initialValue: recipe.tags.joined(separator: ", "))
        _imageUrl = State(initialValue: recipe.imageUrl ?? "")
        _instructions = State(initialValue: recipe.instructions ?? "")
        _lines = State(
            initialValue: recipe.ingredients.map { LooseLine(name: $0.name, quantity: $0.quantity, unit: $0.unit) }
        )
    }

    /// The original lines, in the shape the editor holds them, so "did an
    /// ingredient actually change?" is a straight comparison.
    private var originalLines: [(String, Double?, String?)] {
        recipe.ingredients.map { ($0.name, $0.quantity, $0.unit) }
    }

    private var ingredientsChanged: Bool {
        lines.map { ($0.name, $0.quantity, $0.unit) }.elementsEqual(originalLines) { left, right in
            left.0 == right.0 && left.1 == right.1 && left.2 == right.2
        } == false
    }

    private var trimmedImageUrl: String { imageUrl.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// The field is fine empty (no photo); anything else has to be a URL the
    /// app will actually load, so a typo is caught here rather than showing as
    /// a permanently blank image.
    private var imageUrlIsUsable: Bool { RecipeImageURL.parse(trimmedImageUrl) != nil }

    private var canSave: Bool {
        !title.trimmingCharacters(in: .whitespaces).isEmpty && (trimmedImageUrl.isEmpty || imageUrlIsUsable)
    }

    var body: some View {
        Form {
            Section("Recipe") {
                TextField("Title", text: $title)
                LabeledContent("Servings") {
                    TextField("unknown", text: $servings)
                        .keyboardType(.numberPad)
                        .multilineTextAlignment(.trailing)
                }
                LabeledContent("Prep (min)") {
                    TextField("—", text: $prepMinutes)
                        .keyboardType(.numberPad)
                        .multilineTextAlignment(.trailing)
                }
                LabeledContent("Cook (min)") {
                    TextField("—", text: $cookMinutes)
                        .keyboardType(.numberPad)
                        .multilineTextAlignment(.trailing)
                }
                TextField("Tags, comma separated", text: $tags)
                    .textInputAutocapitalization(.never)
            }

            Section {
                TextField("https://…", text: $imageUrl, axis: .vertical)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .lineLimit(1...3)
                if !trimmedImageUrl.isEmpty {
                    HStack(spacing: 12) {
                        RecipeThumbnail(imageUrl: trimmedImageUrl, size: 64)
                        if imageUrlIsUsable {
                            Text("Preview")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        } else {
                            Text("Needs to start with http:// or https://")
                                .font(.footnote)
                                .foregroundStyle(.red)
                        }
                        Spacer()
                        Button("Remove", role: .destructive) { imageUrl = "" }
                            .font(.footnote)
                    }
                }
            } header: {
                Text("Photo")
            } footer: {
                Text("Ingested recipes usually bring their own photo. Paste a different image address to replace it, or clear the field for none.")
            }

            Section {
                ForEach(lines) { line in
                    EditableLineRow(
                        line: line,
                        onEdit: { editingLine = line },
                        onDelete: { lines.removeAll { $0.id == line.id } }
                    )
                }
                .onMove { lines.move(fromOffsets: $0, toOffset: $1) }
                Button {
                    addingLine = true
                } label: {
                    Label("Add an ingredient", systemImage: "plus.circle")
                }
            } header: {
                Text("Ingredients")
            } footer: {
                if ingredientsChanged {
                    Text("Changing ingredients updates the shopping list for every meal using this recipe. Things you've already ticked off stay ticked.")
                }
            }

            Section("Method") {
                TextField("Instructions", text: $instructions, axis: .vertical)
                    .lineLimit(6...30)
            }

            if let errorMessage {
                Section {
                    Text(errorMessage).foregroundStyle(.red).font(.callout)
                }
            }

            Section {
                Button(action: save) {
                    if isSaving {
                        ProgressView().frame(maxWidth: .infinity)
                    } else {
                        Text("Save changes").frame(maxWidth: .infinity).fontWeight(.semibold)
                    }
                }
                .disabled(isSaving || !canSave)
            } footer: {
                Text("Saved corrections are kept: re-ingesting the source URL won't overwrite them.")
            }
        }
        .navigationTitle("Edit recipe")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Save") { save() }
                    .disabled(isSaving || !canSave)
                    .fontWeight(.semibold)
            }
        }
        .sheet(item: $editingLine) { line in
            NavigationStack {
                IngredientLineEditor(line: line, title: "Edit ingredient") { updated in
                    guard let index = lines.firstIndex(where: { $0.id == line.id }) else { return }
                    lines[index] = updated
                }
            }
        }
        .sheet(isPresented: $addingLine) {
            NavigationStack {
                IngredientLineEditor(line: nil, title: "Add ingredient") { lines.append($0) }
            }
        }
    }

    private func save() {
        isSaving = true
        errorMessage = nil
        let parsedTags = tags.split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
            .filter { !$0.isEmpty }
        Task {
            defer { isSaving = false }
            do {
                let updated = try await store.update(
                    id: recipe.id,
                    title: title.trimmingCharacters(in: .whitespaces),
                    servings: .some(Int(servings)),
                    prepMinutes: .some(Int(prepMinutes)),
                    cookMinutes: .some(Int(cookMinutes)),
                    imageUrl: .some(trimmedImageUrl.isEmpty ? nil : trimmedImageUrl),
                    instructions: .some(instructions.isEmpty ? nil : instructions),
                    tags: parsedTags,
                    ingredients: ingredientsChanged ? lines : nil
                )
                // The server re-synced every meal using this recipe; pull the
                // recalculated list in, since seeing it is the point.
                if ingredientsChanged { await listStore.sync() }
                onSaved(updated)
                dismiss()
            } catch {
                // Unit rejections carry the exact conversion ("2 tbsp → 30 ml")
                // — far more use than "couldn't save".
                errorMessage = error.localizedDescription
            }
        }
    }
}
