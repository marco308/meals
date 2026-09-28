import SwiftUI

@main
struct MealsApp: App {
    @State private var session: Session
    @State private var planStore: PlanStore
    @State private var recipeStore: RecipeStore
    @State private var listStore: ShoppingListStore
    @State private var mealTimes: MealTimes

    init() {
        // Session first: it checks whether the app's data was already on the
        // device, before the stores below create the folder.
        let session = Session()
        let planStore = PlanStore(api: { session.api })
        let recipeStore = RecipeStore(api: { session.api })
        // A token the server no longer accepts signs the app out from the
        // queue's side too; the queue itself is kept for that account.
        let listStore = ShoppingListStore(api: { session.api }, onUnauthorized: { session.logOut() })

        // Everything cached belongs to the account and household that fetched
        // it. The list's cache and queue carry an owner and sort themselves
        // out; the plan and recipe caches don't, so they go whenever the
        // account or household they were read under does.
        session.onAccountChange = { old, new in
            listStore.accountChanged(from: old, to: new)
            if !new.signedIn || (old.owner != nil && new.owner != old.owner) {
                planStore.clearCache()
                recipeStore.clearCache()
            }
        }
        let mealTimes = MealTimes(plan: { planStore.plan }, signedIn: { session.isAuthenticated })
        session.onAccountDeleted = { owner in listStore.accountDeleted(owner) }
        session.announceAccountState()

        _session = State(initialValue: session)
        _planStore = State(initialValue: planStore)
        _recipeStore = State(initialValue: recipeStore)
        _listStore = State(initialValue: listStore)
        _mealTimes = State(initialValue: mealTimes)
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .environment(planStore)
                .environment(recipeStore)
                .environment(listStore)
                .environment(mealTimes)
        }
        // Take a finished meal time off the Lock Screen and schedule the next
        // one, reading the plan fresh when there is signal (the cache if not).
        .backgroundTask(.appRefresh(MealTimes.refreshTaskIdentifier)) {
            await planStore.refresh()
            await mealTimes.refresh()
        }
    }
}

struct RootView: View {
    @Environment(Session.self) private var session
    @Environment(\.scenePhase) private var scenePhase
    @Environment(ShoppingListStore.self) private var listStore
    @Environment(PlanStore.self) private var planStore
    @Environment(MealTimes.self) private var mealTimes

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
        // Meal times on the Lock Screen follow the plan: a meal cooked, added
        // or removed (here or by anyone in the household) and a sign-out.
        .onChange(of: planStore.plan) { Task { await mealTimes.refresh() } }
        .onChange(of: session.isAuthenticated) { Task { await mealTimes.refresh() } }
        .task {
            // Signal back (walking out of the dead spot by the freezers):
            // send what was queued now, not on the next tap.
            for await _ in Connectivity.restored() {
                await refreshAndSync()
            }
        }
        .onChange(of: scenePhase) { _, phase in
            // Coming back to the foreground is the natural sync point for
            // anything queued while offline — and the natural moment to notice
            // that the server has moved on without us.
            if phase == .active {
                Task { await session.checkClientCompatibility() }
                Task { await refreshAndSync() }
                Task {
                    await mealTimes.refresh()
                    if session.isAuthenticated { await planStore.refresh() }
                }
            }
        }
    }

    /// Ask the server who we are, then send the queue as that. A household
    /// change made on another device (the lead removing us) has to reach the
    /// queue before the queue reaches the server.
    private func refreshAndSync() async {
        guard session.isAuthenticated else { return }
        await session.restore()
        await listStore.sync()
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
