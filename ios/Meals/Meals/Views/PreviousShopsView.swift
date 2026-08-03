import SwiftUI

/// Finished shops, newest first — what a list looked like when "finish the
/// shop" archived it. A record, not a workspace: nothing here is editable.
struct PreviousShopsView: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    @State private var lists: [ArchivedListSummary] = []
    @State private var loaded = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            List {
                ForEach(lists) { list in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(TimestampLabel.day(list.archivedAt) ?? "Unknown date")
                        Text(started(list))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                if loaded && lists.isEmpty {
                    ContentUnavailableView(
                        "No previous shops yet",
                        systemImage: "checkmark.seal",
                        description: Text("Finish a shop and it lands here, for the record.")
                    )
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.red).font(.callout)
                    }
                }
            }
            .navigationTitle("Previous shops")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .task { await load() }
        }
    }

    private func started(_ list: ArchivedListSummary) -> String {
        let items = "\(list.itemCount) item\(list.itemCount == 1 ? "" : "s")"
        guard let started = TimestampLabel.day(list.createdAt) else { return items }
        return "started \(started) · \(items)"
    }

    private func load() async {
        do {
            lists = try await session.api.archivedLists()
            loaded = true
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
