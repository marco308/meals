import SwiftUI

struct LoginView: View {
    @Environment(Session.self) private var session

    @State private var email = ""
    @State private var password = ""
    @State private var displayName = ""
    @State private var inviteCode = ""
    @State private var isRegistering = false
    @State private var showForgotPassword = false
    @State private var isWorking = false
    @State private var errorMessage: String?

    var body: some View {
        @Bindable var session = session
        NavigationStack {
            Form {
                Section {
                    TextField("Email", text: $email)
                        .textContentType(.emailAddress)
                        .keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("Password", text: $password)
                        .textContentType(isRegistering ? .newPassword : .password)
                    if isRegistering {
                        TextField("Your name", text: $displayName)
                            .textContentType(.name)
                    }
                }

                if isRegistering {
                    Section {
                        TextField("Invite code (optional)", text: $inviteCode)
                            .textInputAutocapitalization(.characters)
                            .autocorrectionDisabled()
                            .font(.callout.monospaced())
                    } footer: {
                        Text(
                            "Joining someone's household? Enter their invite code to share "
                                + "their recipes, plan and shopping list. Leave it blank to start your own."
                        )
                    }
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .foregroundStyle(.red)
                            .font(.callout)
                    }
                }

                Section {
                    Button(action: submit) {
                        if isWorking {
                            ProgressView().frame(maxWidth: .infinity)
                        } else {
                            Text(isRegistering ? "Create account" : "Log in")
                                .frame(maxWidth: .infinity)
                                .fontWeight(.semibold)
                        }
                    }
                    .disabled(isWorking || email.isEmpty || password.isEmpty || (isRegistering && displayName.isEmpty))

                    Button(isRegistering ? "I already have an account" : "Create a new account") {
                        isRegistering.toggle()
                        errorMessage = nil
                    }
                    .font(.callout)

                    if !isRegistering {
                        Button("Forgot password?") { showForgotPassword = true }
                            .font(.callout)
                    }
                }

                // Last, not first: most people never touch it, and a URL box
                // above the password field reads as a wall rather than a
                // setting. But it is the one thing nobody can guess, so it
                // explains itself rather than sitting there unlabelled.
                Section {
                    TextField("Server URL", text: $session.serverURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.callout.monospaced())
                    Link(destination: AppLinks.support(server: session.serverURL)) {
                        Label("How do I get a server?", systemImage: "questionmark.circle")
                            .font(.callout)
                    }
                } header: {
                    Text("Server")
                } footer: {
                    Text(
                        "Meals has no cloud. Your recipes, plan and shopping list live on a "
                            + "server you run — it's free, open source and one command to start. "
                            + "Already in a household? Use its server's address and an invite code."
                    )
                }
            }
            .navigationTitle("Meals")
            .sheet(isPresented: $showForgotPassword) { ForgotPasswordView(initialEmail: email) }
        }
    }

    private func submit() {
        isWorking = true
        errorMessage = nil
        Task {
            defer { isWorking = false }
            do {
                if isRegistering {
                    try await session.register(
                        email: email, password: password, displayName: displayName, inviteCode: inviteCode
                    )
                } else {
                    try await session.logIn(email: email, password: password)
                }
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
