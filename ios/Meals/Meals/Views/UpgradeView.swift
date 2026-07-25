import SwiftUI

/// Shown instead of the app when the server refuses this build (426). The
/// server keeps the shopping-list endpoints open to old clients precisely so
/// this screen can still flush what was queued offline — a blocked app that
/// couldn't sync would lose the user's ticked-off items, which is a far worse
/// outcome than a stale UI.
struct UpgradeRequiredView: View {
    @Environment(ShoppingListStore.self) private var listStore
    @Environment(Session.self) private var session

    let detail: String
    let upgradeURL: String?

    @State private var isRechecking = false

    var body: some View {
        VStack(spacing: 24) {
            Image(systemName: "arrow.up.circle")
                .font(.system(size: 56))
                .foregroundStyle(.tint)

            Text("Update Meals")
                .font(.title.bold())

            // The server writes its 4xx bodies for the user to read.
            Text(detail)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)

            syncStatus

            VStack(spacing: 12) {
                if let upgradeURL, let url = URL(string: upgradeURL) {
                    Link("Get the new version", destination: url)
                        .buttonStyle(.borderedProminent)
                }
                Button("Check again") {
                    Task {
                        isRechecking = true
                        await session.checkClientCompatibility()
                        isRechecking = false
                    }
                }
                .disabled(isRechecking)
            }
        }
        .padding(32)
        .task {
            // Drain anything held on disk before the user goes to update.
            await listStore.sync()
        }
    }

    @ViewBuilder
    private var syncStatus: some View {
        if listStore.isSyncing {
            Label("Saving your changes…", systemImage: "arrow.triangle.2.circlepath")
                .font(.callout)
                .foregroundStyle(.secondary)
        } else if !listStore.pending.isEmpty {
            Label(
                "\(listStore.pending.count) change\(listStore.pending.count == 1 ? "" : "s") still to sync — "
                    + "they're saved on this device.",
                systemImage: "clock.arrow.circlepath"
            )
            .font(.callout)
            .foregroundStyle(.orange)
            .multilineTextAlignment(.center)
        } else {
            Label("All your changes are saved to the server.", systemImage: "checkmark.circle")
                .font(.callout)
                .foregroundStyle(.green)
        }
    }
}

/// The soft case: this build still works, but a newer one is out.
struct UpgradeBanner: View {
    @Environment(Session.self) private var session

    let upgradeURL: String?

    var body: some View {
        HStack {
            Image(systemName: "arrow.up.circle.fill")
            Text("A new version of Meals is available")
                .font(.callout)
            Spacer()
            if let upgradeURL, let url = URL(string: upgradeURL) {
                Link("Update", destination: url).font(.callout.bold())
            }
            Button {
                session.dismissUpgradeNudge()
            } label: {
                Image(systemName: "xmark")
            }
            .accessibilityLabel("Dismiss")
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.thinMaterial)
    }
}
