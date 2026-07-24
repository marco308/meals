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
            if session.isAuthenticated {
                MainTabView()
                    .task { await session.restore() }
            } else {
                LoginView()
            }
        }
        .onChange(of: scenePhase) { _, phase in
            // Coming back to the foreground is the natural sync point for
            // anything queued while offline.
            if phase == .active && session.isAuthenticated {
                Task { await listStore.sync() }
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
        }
    }
}
