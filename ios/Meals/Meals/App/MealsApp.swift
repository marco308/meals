import SwiftUI

@main
struct MealsApp: App {
    @State private var session: Session
    @State private var planStore: PlanStore
    @State private var recipeStore: RecipeStore
    @State private var listStore: ShoppingListStore

    init() {
        let session = Session()
        _session = State(initialValue: session)
        _planStore = State(initialValue: PlanStore(api: { session.api }))
        _recipeStore = State(initialValue: RecipeStore(api: { session.api }))
        _listStore = State(initialValue: ShoppingListStore(api: { session.api }))
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .environment(planStore)
                .environment(recipeStore)
                .environment(listStore)
        }
    }
}

struct RootView: View {
    @Environment(Session.self) private var session
    @Environment(\.scenePhase) private var scenePhase
    @Environment(ShoppingListStore.self) private var listStore

    var body: some View {
        Group {
            // A build the server has cut off can't be trusted to render or
            // write anything correctly, so it gets one screen and no tabs.
            if case .required(let detail, let url) = session.upgrade {
                UpgradeRequiredView(detail: detail, upgradeURL: url)
            } else if session.isAuthenticated {
                MainTabView()
                    .task { await session.restore() }
                    .safeAreaInset(edge: .top) {
                        if case .available(let url) = session.upgrade {
                            UpgradeBanner(upgradeURL: url)
                        }
                    }
            } else {
                LoginView()
            }
        }
        .task { await session.checkClientCompatibility() }
        .onChange(of: scenePhase) { _, phase in
            // Coming back to the foreground is the natural sync point for
            // anything queued while offline — and the natural moment to notice
            // that the server has moved on without us.
            if phase == .active {
                Task { await session.checkClientCompatibility() }
                if session.isAuthenticated {
                    Task { await listStore.sync() }
                }
            }
        }
    }
}

struct MainTabView: View {
    var body: some View {
        TabView {
            PlanView()
                .tabItem { Label("Plan", systemImage: "list.bullet.rectangle") }
            RecipesView()
                .tabItem { Label("Recipes", systemImage: "book") }
            ShoppingListView()
                .tabItem { Label("Shopping", systemImage: "cart") }
            IngredientsView()
                .tabItem { Label("Ingredients", systemImage: "carrot") }
            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
    }
}
