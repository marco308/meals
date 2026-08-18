import SwiftUI

struct LoginView: View {
    @Environment(Session.self) private var session

    @State private var email = ""
    @State private var password = ""
    @State private var displayName = ""
    @State private var inviteCode = ""
    @State private var householdName = ""
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
                        // Only offered while starting a household: with a code
                        // you are joining one that is already named, and the
                        // server ignores the field.
                        if inviteCode.trimmingCharacters(in: .whitespaces).isEmpty {
                            TextField("Household name (optional)", text: $householdName)
                                .textContentType(.organizationName)
                        }
                    } footer: {
                        Text(
                            "Joining someone's household? Enter their invite code to share "
                                + "their recipes, plan and shopping list. Leave it blank to start your "
                                + "own, named whatever you like — “Home” if you'd rather not choose."
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

                // The primary action gets to look like one: filled, full width,
                // on the page rather than inside a grouped card. As a plain row
                // it read as a label, and centring the text pushed the row
                // separator's inset to the middle of the screen, which looked
                // like a rendering fault.
                Section {
                    Button(action: submit) {
                        if isWorking {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity)
                        } else {
                            Text(isRegistering ? "Create account" : "Log in")
                                .frame(maxWidth: .infinity)
                                .fontWeight(.semibold)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(isWorking || email.isEmpty || password.isEmpty || (isRegistering && displayName.isEmpty))
                    .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: 0, trailing: 0))
                    .listRowBackground(Color.clear)
                }

                Section {
                    Button(isRegistering ? "I already have an account" : "Create a new account") {
                        isRegistering.toggle()
                        errorMessage = nil
                    }
                    .font(.callout)

                    // Hidden on a server with no SMTP configured, which is the
                    // normal state of a fresh self-hosted one: the endpoint
                    // 503s there, and a button that cannot work is better
                    // hidden than explained.
                    if !isRegistering && session.canResetPassword {
                        Button("Forgot password?") { showForgotPassword = true }
                            .font(.callout)
                    }
                }

                // Last, not first: most people never touch it, and a URL box
                // above the password field reads as a wall rather than a
                // setting. But it is the one thing nobody can guess, so it
                // explains itself rather than sitting there unlabelled.
                Section {
                    // Shrinks rather than truncates: at accessibility text
                    // sizes a monospaced URL outgrows the row, and a server
                    // address cut off mid-host ("https://meals.marcusla…") is
                    // unreadable exactly when someone is trying to check it.
                    TextField("Server URL", text: $session.serverURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.callout.monospaced())
                        .lineLimit(1)
                        .minimumScaleFactor(0.5)
                    Link(destination: AppLinks.support(server: session.serverURL)) {
                        // Not a Label: at accessibility sizes its default
                        // layout puts the icon alone on the first line and
                        // wraps the text underneath it. Aligning to the first
                        // baseline keeps the icon beside the text and lets the
                        // text wrap in its own column.
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Image(systemName: "questionmark.circle")
                            Text("How do I get a server?")
                                .fixedSize(horizontal: false, vertical: true)
                        }
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
                        email: email,
                        password: password,
                        displayName: displayName,
                        inviteCode: inviteCode,
                        householdName: householdName.trimmingCharacters(in: .whitespaces)
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
