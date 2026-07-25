import SwiftUI

/// Change the signed-in user's password. The server takes the current password
/// as proof, revokes every session token, and hands back a fresh one — so this
/// device stays logged in and any other device has to sign in again.
struct ChangePasswordView: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    @State private var currentPassword = ""
    @State private var newPassword = ""
    @State private var confirmPassword = ""
    @State private var isSaving = false
    @State private var errorMessage: String?
    @State private var didSucceed = false

    private static let minimumLength = 8  // matches the API's own floor

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
        !isSaving
            && !currentPassword.isEmpty
            && newPassword.count >= Self.minimumLength
            && confirmPassword == newPassword
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    SecureField("Current password", text: $currentPassword)
                        .textContentType(.password)
                    SecureField("New password", text: $newPassword)
                        .textContentType(.newPassword)
                    SecureField("Confirm new password", text: $confirmPassword)
                        .textContentType(.newPassword)
                } footer: {
                    Text("At least \(Self.minimumLength) characters. Your other devices will be signed out.")
                }

                if let message = validationMessage ?? errorMessage {
                    Section {
                        Text(message)
                            .foregroundStyle(.red)
                            .font(.callout)
                    }
                }

                Section {
                    Button(action: submit) {
                        if isSaving {
                            ProgressView().frame(maxWidth: .infinity)
                        } else {
                            Text("Change password").frame(maxWidth: .infinity).fontWeight(.semibold)
                        }
                    }
                    .disabled(!canSubmit)
                }
            }
            .navigationTitle("Change password")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .alert("Password changed", isPresented: $didSucceed) {
                Button("Done") { dismiss() }
            } message: {
                Text("You're still signed in here. Any other device will need the new password.")
            }
        }
    }

    private func submit() {
        isSaving = true
        errorMessage = nil
        Task {
            defer { isSaving = false }
            do {
                try await session.changePassword(current: currentPassword, new: newPassword)
                currentPassword = ""
                newPassword = ""
                confirmPassword = ""
                didSucceed = true
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
