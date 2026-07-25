import SwiftUI

/// Reset a forgotten password with a code emailed by the server (decision Q20).
///
/// Two steps in one sheet, because the code arrives in seconds and bouncing the
/// user between screens to fetch it loses the thread.
///
/// Note what the first step deliberately does *not* say: the server answers the
/// same way whether or not the address has an account, so that it can't be used
/// to find out who's registered. The wording here has to preserve that — "if
/// that address has an account", never "check your email".
struct ForgotPasswordView: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    let initialEmail: String

    private enum Step { case requestCode, enterCode }

    @State private var step: Step = .requestCode
    @State private var email: String
    @State private var code = ""
    @State private var newPassword = ""
    @State private var confirmPassword = ""
    @State private var isWorking = false
    @State private var errorMessage: String?

    private static let minimumLength = 8  // matches the API's own floor

    init(initialEmail: String = "") {
        self.initialEmail = initialEmail
        _email = State(initialValue: initialEmail)
    }

    private var validationMessage: String? {
        if !newPassword.isEmpty && newPassword.count < Self.minimumLength {
            return "New password must be at least \(Self.minimumLength) characters."
        }
        if !confirmPassword.isEmpty && confirmPassword != newPassword {
            return "The new passwords don't match."
        }
        return nil
    }

    private var canSubmit: Bool {
        if isWorking { return false }
        switch step {
        case .requestCode:
            return !email.isEmpty
        case .enterCode:
            return !code.isEmpty && newPassword.count >= Self.minimumLength && confirmPassword == newPassword
        }
    }

    var body: some View {
        NavigationStack {
            Form {
                switch step {
                case .requestCode:
                    Section {
                        TextField("Email", text: $email)
                            .textContentType(.emailAddress)
                            .keyboardType(.emailAddress)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    } footer: {
                        Text("We'll email a reset code if that address has an account here.")
                    }

                case .enterCode:
                    Section {
                        TextField("Reset code", text: $code)
                            .textInputAutocapitalization(.characters)
                            .autocorrectionDisabled()
                            .font(.callout.monospaced())
                        SecureField("New password", text: $newPassword)
                            .textContentType(.newPassword)
                        SecureField("Confirm new password", text: $confirmPassword)
                            .textContentType(.newPassword)
                    } header: {
                        Text("Check your email")
                    } footer: {
                        Text(
                            "If \(email) has an account, a code is on its way. It works once "
                                + "and expires shortly. At least \(Self.minimumLength) characters "
                                + "for the new password; your other devices will be signed out."
                        )
                    }
                }

                if let message = validationMessage ?? errorMessage {
                    Section {
                        Text(message).foregroundStyle(.red).font(.callout)
                    }
                }

                Section {
                    Button(action: submit) {
                        if isWorking {
                            ProgressView().frame(maxWidth: .infinity)
                        } else {
                            Text(step == .requestCode ? "Email me a code" : "Set new password")
                                .frame(maxWidth: .infinity)
                                .fontWeight(.semibold)
                        }
                    }
                    .disabled(!canSubmit)

                    if step == .enterCode {
                        Button("Send a new code") {
                            step = .requestCode
                            code = ""
                            errorMessage = nil
                        }
                        .font(.callout)
                    }
                }
            }
            .navigationTitle("Reset password")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }

    private func submit() {
        isWorking = true
        errorMessage = nil
        Task {
            defer { isWorking = false }
            do {
                switch step {
                case .requestCode:
                    try await session.requestPasswordReset(email: email)
                    step = .enterCode
                case .enterCode:
                    // Signs this device in on success, so the sheet closing
                    // lands the user in the app rather than back at the login.
                    try await session.confirmPasswordReset(code: code, newPassword: newPassword)
                    dismiss()
                }
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
