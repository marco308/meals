import SwiftUI

/// Permanently delete the signed-in account (decision Q20).
///
/// This exists in the app, not just in the API, because App Store review
/// requires an app that creates accounts to offer deletion from inside it.
///
/// Two guards before the button does anything: the current password, and typing
/// DELETE. That's deliberately more friction than changing a password — there is
/// no undo and no grace period on the server side.
struct DeleteAccountView: View {
    @Environment(Session.self) private var session
    @Environment(\.dismiss) private var dismiss

    /// Cleared before the account goes, for the same reason logging out clears
    /// them: cached reads belong to the login that fetched them.
    let clearCaches: () -> Void

    @State private var password = ""
    @State private var confirmation = ""
    @State private var isDeleting = false
    @State private var errorMessage: String?

    private static let phrase = "DELETE"

    private var canSubmit: Bool {
        !isDeleting && !password.isEmpty && confirmation.trimmingCharacters(in: .whitespaces) == Self.phrase
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text(
                        "Deleting your account removes your sign-in and every API token you've "
                            + "created. If you're the last person in your household, its recipes, "
                            + "meals, plans, shopping list and cooked history are deleted too."
                    )
                    .font(.callout)
                } header: {
                    Text("This cannot be undone")
                }

                Section {
                    SecureField("Current password", text: $password)
                        .textContentType(.password)
                    TextField("Type \(Self.phrase) to confirm", text: $confirmation)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.red).font(.callout)
                    }
                }

                Section {
                    Button(role: .destructive, action: submit) {
                        if isDeleting {
                            ProgressView().frame(maxWidth: .infinity)
                        } else {
                            Text("Delete my account")
                                .frame(maxWidth: .infinity)
                                .fontWeight(.semibold)
                        }
                    }
                    .disabled(!canSubmit)
                }
            }
            .navigationTitle("Delete account")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }

    private func submit() {
        isDeleting = true
        errorMessage = nil
        Task {
            defer { isDeleting = false }
            do {
                clearCaches()
                // Session.deleteAccount logs out on success, which swaps the
                // root view back to the login screen underneath this sheet.
                try await session.deleteAccount(password: password)
                dismiss()
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
