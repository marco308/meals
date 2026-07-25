import SwiftUI

struct LoginView: View {
    @Environment(Session.self) private var session

    @State private var email = ""
    @State private var password = ""
    @State private var displayName = ""
    @State private var inviteCode = ""
    @State private var isRegistering = false
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
                }

                Section("Server") {
                    TextField("Server URL", text: $session.serverURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.callout.monospaced())
                }
            }
            .navigationTitle("Meals")
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
