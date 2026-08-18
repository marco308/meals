import XCTest
@testable import Meals

/// The account surfaces the App Store cares about: which server the app talks
/// to, where its policy and support pages are, and what it does with a server
/// that answers differently from the one it was built against.
final class AccountTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try APIClient.decoder().decode(type, from: XCTUnwrap(json.data(using: .utf8)))
    }

    // MARK: - Profile decoding

    func testDecodesHouseholdAlongsideTheAccount() throws {
        let user = try decode(
            UserProfile.self,
            """
            {"id": "7b7f4a6e-0e5d-4f4a-9a7f-2f1c6c9c0a11", "email": "you@example.com",
             "display_name": "You", "household_id": "1f2e3d4c-5b6a-4978-8869-0a1b2c3d4e5f",
             "household_name": "Home"}
            """
        )
        XCTAssertEqual(user.householdName, "Home")
        XCTAssertNotNil(user.householdId)
    }

    func testProfileDecodesWithoutHouseholdFields() throws {
        // A shipped build can outlive the server it was written for, and a
        // pre-Q19 server sends neither field. Missing must never be a decode
        // error — that would sign the user out of a working account.
        let user = try decode(
            UserProfile.self,
            """
            {"id": "7b7f4a6e-0e5d-4f4a-9a7f-2f1c6c9c0a11", "email": "you@example.com", "display_name": "You"}
            """
        )
        XCTAssertNil(user.householdName)
        XCTAssertNil(user.householdId)
        XCTAssertEqual(user.email, "you@example.com")
    }

    // MARK: - The household and its lead (Q23)

    func testDecodesTheHouseholdAndItsMembers() throws {
        let household = try decode(
            Household.self,
            """
            {"id": "1f2e3d4c-5b6a-4978-8869-0a1b2c3d4e5f", "name": "Williams",
             "created_at": "2026-01-04T09:00:00Z",
             "lead_user_id": "7b7f4a6e-0e5d-4f4a-9a7f-2f1c6c9c0a11",
             "members": [
               {"id": "7b7f4a6e-0e5d-4f4a-9a7f-2f1c6c9c0a11", "display_name": "Marcus",
                "email": "marcus@example.com", "created_at": "2026-01-04T09:00:00Z",
                "is_lead": true, "invited_by_user_id": null},
               {"id": "2c2c2c2c-0e5d-4f4a-9a7f-2f1c6c9c0a22", "display_name": "Isla",
                "email": "isla@example.com", "created_at": "2026-02-01T09:00:00Z",
                "is_lead": false, "invited_by_user_id": "7b7f4a6e-0e5d-4f4a-9a7f-2f1c6c9c0a11"}
             ]}
            """
        )
        XCTAssertEqual(household.name, "Williams")
        XCTAssertEqual(household.members.count, 2)
        XCTAssertEqual(household.members.first?.isLead, true)
        XCTAssertNil(household.members.first?.invitedByUserId)
        XCTAssertEqual(household.members.last?.invitedByUserId, household.leadUserId)
    }

    func testProfileKnowsWhetherItLeads() throws {
        let json = """
            {"id": "7b7f4a6e-0e5d-4f4a-9a7f-2f1c6c9c0a11", "email": "you@example.com",
             "display_name": "You", "household_id": "1f2e3d4c-5b6a-4978-8869-0a1b2c3d4e5f",
             "household_name": "Home", "household_lead_user_id": "%@"}
            """
        let lead = try decode(
            UserProfile.self, json.replacingOccurrences(of: "%@", with: "7b7f4a6e-0e5d-4f4a-9a7f-2f1c6c9c0a11")
        )
        let member = try decode(
            UserProfile.self, json.replacingOccurrences(of: "%@", with: "2c2c2c2c-0e5d-4f4a-9a7f-2f1c6c9c0a22")
        )
        XCTAssertTrue(lead.leadsHousehold)
        XCTAssertFalse(member.leadsHousehold)
    }

    func testAProfileFromAServerWithoutALeadDoesNotClaimToLead() throws {
        // A build newer than its server (Q23 shipped after this app did) must
        // not offer the invite and remove controls on a guess. Absent means no.
        let user = try decode(
            UserProfile.self,
            """
            {"id": "7b7f4a6e-0e5d-4f4a-9a7f-2f1c6c9c0a11", "email": "you@example.com",
             "display_name": "You", "household_id": "1f2e3d4c-5b6a-4978-8869-0a1b2c3d4e5f"}
            """
        )
        XCTAssertNil(user.householdLeadUserId)
        XCTAssertFalse(user.leadsHousehold)
    }

    func testDecodesTheResultOfLeavingAHousehold() throws {
        let result = try decode(
            MemberRemoved.self,
            """
            {"removed_user_id": "7b7f4a6e-0e5d-4f4a-9a7f-2f1c6c9c0a11", "you_left": true,
             "detail": "you have left"}
            """
        )
        XCTAssertTrue(result.youLeft)
        XCTAssertEqual(result.detail, "you have left")
    }

    func testDecodesAnInviteCode() throws {
        let invite = try decode(
            InviteCreated.self,
            """
            {"id": "9c8b7a6d-5e4f-4321-8765-0a1b2c3d4e5f", "code": "K7QM-2XPD",
             "created_at": "2026-07-26T09:00:00Z", "expires_at": "2026-08-02T09:00:00Z",
             "accepted_at": null, "accepted_by_user_id": null}
            """
        )
        XCTAssertEqual(invite.code, "K7QM-2XPD")
        XCTAssertEqual(invite.expiryLabel, "2 August 2026")
    }

    func testAnUnreadableExpiryIsNotAnError() throws {
        // Timestamps stay strings app-wide precisely so a server that changes
        // its date format can't turn a working screen into a decode failure —
        // the sheet just drops the expiry sentence.
        let invite = try decode(
            InviteCreated.self,
            """
            {"id": "9c8b7a6d-5e4f-4321-8765-0a1b2c3d4e5f", "code": "K7QM-2XPD", "expires_at": "next Tuesday"}
            """
        )
        XCTAssertNil(invite.expiryLabel)
    }

    // MARK: - Where the app points

    func testFreshInstallPointsAtAServerThatIsActuallyUp() {
        // A public download that opens on a dead localhost URL looks broken
        // before the user has typed anything.
        XCTAssertEqual(AppLinks.defaultServerURL, "https://meals.marcuslab.uk")
        XCTAssertTrue(AppLinks.defaultServerURL.hasPrefix("https://"))
    }

    func testPolicyLinksFollowTheConnectedServer() {
        // The operator of your data is whoever runs your server, so that's
        // whose policy Settings should open — every deployment serves both.
        XCTAssertEqual(
            AppLinks.privacy(server: "https://meals.example.com").absoluteString,
            "https://meals.example.com/privacy"
        )
        XCTAssertEqual(
            AppLinks.support(server: "http://localhost:8000").absoluteString,
            "http://localhost:8000/support"
        )
    }

    func testPolicyLinksSurviveAnUnusableServerField() {
        // The field is free text and can hold anything mid-edit; a dead link in
        // Settings is worse than one pointing at the default deployment.
        for junk in ["", "   ", "not a url", "ftp:", "meals.example.com"] {
            XCTAssertEqual(
                AppLinks.privacy(server: junk).absoluteString,
                AppLinks.defaultServerURL + "/privacy",
                "expected a usable fallback for \(junk.debugDescription)"
            )
        }
    }

    func testVersionReadsAsSomethingYouCanQuoteInABugReport() {
        XCTAssertTrue(
            ClientIdentity.displayVersion.contains("("),
            "Settings shows '<version> (<build>)'; got \(ClientIdentity.displayVersion)"
        )
    }
}
