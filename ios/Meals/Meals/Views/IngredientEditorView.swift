import SwiftUI

/// The one place to see and edit an ingredient's metadata: canonical name,
/// supermarket aisle, staple flag. Reached from any ingredient row (recipe,
/// meal, shopping item). Changes are saved immediately and ripple everywhere
/// the ingredient appears — one canonical ingredient per name is the point.
struct IngredientEditorView: View {
    @Environment(Session.self) private var session
    @Environment(ShoppingListStore.self) private var listStore

    let ingredientId: UUID
    var onChange: (() -> Void)? = nil

    @State private var info: IngredientInfo?
    @State private var aisles: [Aisle] = []
    @State private var errorMessage: String?
    @State private var isSaving = false

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
    }

    private var stapleBinding: Binding<Bool> {
        Binding(
            get: { info?.isStaple ?? false },
            set: { value in save(isStaple: value) }
        )
    }

    private func load() async {
        do {
            info = try await session.api.ingredient(id: ingredientId)
            aisles = try await session.api.fetchAisles()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func save(aisle: String? = nil, isStaple: Bool? = nil) {
        isSaving = true
        errorMessage = nil
        Task {
            defer { isSaving = false }
            do {
                info = try await session.api.updateIngredient(id: ingredientId, aisle: aisle, isStaple: isStaple)
                await listStore.sync()  // aisle/staple changes re-sort and re-filter the list
                onChange?()
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
