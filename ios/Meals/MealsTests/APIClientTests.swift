import XCTest
@testable import Meals

final class ErrorDetailTests: XCTestCase {
    func testStringDetailPassesThrough() {
        let data = Data(#"{"detail": "meal 'Spag bol' is already in plan 'w/c 20 July'"}"#.utf8)
        XCTAssertEqual(APIClient.errorDetail(from: data), "meal 'Spag bol' is already in plan 'w/c 20 July'")
    }

    func testValidationListJoinsMessages() {
        let data = Data(#"{"detail": [{"msg": "unit 'tbsp' is not accepted"}, {"msg": "quantity must be positive"}]}"#.utf8)
        XCTAssertEqual(APIClient.errorDetail(from: data), "unit 'tbsp' is not accepted\nquantity must be positive")
    }

    func testGarbageReturnsNil() {
        XCTAssertNil(APIClient.errorDetail(from: Data("not json".utf8)))
    }
}

// MARK: - URLProtocol-backed tests

extension URLRequest {
    /// URLSession hands URLProtocol the body as a stream, not httpBody.
    func streamedBody() -> Data? {
        guard let stream = httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        let bufferSize = 4096
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
        defer { buffer.deallocate() }
        while stream.hasBytesAvailable {
            let read = stream.read(buffer, maxLength: bufferSize)
            if read <= 0 { break }
            data.append(buffer, count: read)
        }
        return data
    }
}

final class StubProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: (@Sendable (URLRequest) -> (Int, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = Self.handler else { return }
        let (status, data) = handler(request)
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

final class FailingProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet))
    }
    override func stopLoading() {}
}

final class APIClientTests: XCTestCase {
    private func client(protocolClass: AnyClass) -> APIClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [protocolClass]
        return APIClient(
            baseURL: URL(string: "http://testserver")!,
            token: "meals_test-token",
            session: URLSession(configuration: config)
        )
    }

    func testBearerTokenAndDecoding() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer meals_test-token")
            XCTAssertEqual(request.url?.path, "/aisles")
            return (200, Data(#"[{"emoji": "🥬", "label": "Fruit & veg"}]"#.utf8))
        }
        let aisles = try await client(protocolClass: StubProtocol.self).fetchAisles()
        XCTAssertEqual(aisles, [Aisle(emoji: "🥬", label: "Fruit & veg")])
    }

    func testServerErrorSurfacesDetailVerbatim() async {
        StubProtocol.handler = { _ in
            (409, Data(#"{"detail": "plan 'w/c' is archived; create a new plan via POST /plans"}"#.utf8))
        }
        do {
            _ = try await client(protocolClass: StubProtocol.self).fetchAisles()
            XCTFail("expected an error")
        } catch let error as APIError {
            XCTAssertEqual(error, .server(status: 409, detail: "plan 'w/c' is archived; create a new plan via POST /plans"))
        } catch {
            XCTFail("unexpected error type: \(error)")
        }
    }

    func testUnauthorizedMapsToUnauthorized() async {
        StubProtocol.handler = { _ in (401, Data(#"{"detail": "invalid or revoked token"}"#.utf8)) }
        do {
            _ = try await client(protocolClass: StubProtocol.self).fetchAisles()
            XCTFail("expected an error")
        } catch let error as APIError {
            XCTAssertEqual(error, .unauthorized(detail: "invalid or revoked token"))
        } catch {
            XCTFail("unexpected error type: \(error)")
        }
    }

    func testUpdateIngredientPatchesOnlyGivenFields() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "PATCH")
            XCTAssertTrue(request.url!.path.hasPrefix("/ingredients/"))
            let body = request.httpBody ?? request.streamedBody()
            let sent = try? JSONSerialization.jsonObject(with: body ?? Data()) as? [String: Any]
            XCTAssertEqual(sent?["aisle"] as? String, "🥫")
            XCTAssertNil(sent?["is_staple"], "unset fields are not sent")
            return (
                200,
                Data(
                    #"{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "name": "tartare sauce", "aisle": "🥫", "aisle_label": "Tins & jars", "is_staple": false}"#
                    .utf8
                )
            )
        }
        let updated = try await client(protocolClass: StubProtocol.self)
            .updateIngredient(id: UUID(), aisle: "🥫")
        XCTAssertEqual(updated.aisle, "🥫")
        XCTAssertEqual(updated.aisleLabel, "Tins & jars")
    }

    func testDeleteRecipeSendsDeleteAndAcceptsEmptyBody() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "DELETE")
            XCTAssertEqual(request.url?.path, "/recipes/61931d47-4154-418a-a43f-f734a0e3d888")
            return (204, Data())
        }
        try await client(protocolClass: StubProtocol.self)
            .deleteRecipe(id: UUID(uuidString: "61931D47-4154-418A-A43F-F734A0E3D888")!)
    }

    func testDeleteRecipeInUseSurfacesThe409() async {
        StubProtocol.handler = { _ in
            (409, Data(#"{"detail": "recipe is used by one or more meals; remove it from those meals first"}"#.utf8))
        }
        do {
            try await client(protocolClass: StubProtocol.self).deleteRecipe(id: UUID())
            XCTFail("expected an error")
        } catch let APIError.server(status, detail) {
            XCTAssertEqual(status, 409)
            XCTAssertTrue(detail.contains("used by one or more meals"))
        } catch {
            XCTFail("unexpected error type: \(error)")
        }
    }

    func testRecipeSortIsOmittedForTheDefaultOrder() async throws {
        StubProtocol.handler = { request in
            XCTAssertNil(request.url?.query, "the API default needs no parameter, so old backends keep working")
            return (200, Data("[]".utf8))
        }
        _ = try await client(protocolClass: StubProtocol.self).recipes(search: nil)

        StubProtocol.handler = { request in
            XCTAssertEqual(request.url?.query, "sort=most_cooked")
            return (200, Data("[]".utf8))
        }
        _ = try await client(protocolClass: StubProtocol.self).recipes(search: nil, sort: .mostCooked)
    }

    func testUpdateMealSendsOnlyTheFieldsGiven() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "PATCH")
            XCTAssertEqual(request.url?.path, "/meals/61931d47-4154-418a-a43f-f734a0e3d888")
            let body = request.httpBody ?? request.streamedBody()
            let sent = try? JSONSerialization.jsonObject(with: body ?? Data()) as? [String: Any]
            XCTAssertEqual(sent?["name"] as? String, "Cottage pie with peas")
            XCTAssertNil(sent?["recipe_ids"], "a rename must not blank the composition")
            XCTAssertNil(sent?["loose_ingredients"])
            return (
                200,
                Data(
                    #"{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "name": "Cottage pie with peas", "slot": "dinner", "recipes": [], "loose_ingredients": [], "created_at": "2026-07-25T09:00:00Z", "times_cooked": 2, "last_cooked_at": "2026-07-19T18:00:00Z"}"#
                    .utf8
                )
            )
        }
        let meal = try await client(protocolClass: StubProtocol.self).updateMeal(
            id: UUID(uuidString: "61931D47-4154-418A-A43F-F734A0E3D888")!,
            name: "Cottage pie with peas"
        )
        XCTAssertEqual(meal.name, "Cottage pie with peas")
        XCTAssertEqual(meal.timesCooked, 2)
        XCTAssertEqual(meal.cookedSummary, "cooked 2× · last Jul 2026")
    }

    func testUpdateMealSendsReplacementListsWhenGiven() async throws {
        StubProtocol.handler = { request in
            let body = request.httpBody ?? request.streamedBody()
            let sent = try? JSONSerialization.jsonObject(with: body ?? Data()) as? [String: Any]
            // Always the richer `recipes` shape — the API rejects both fields
            // together, and an unscaled meal is just every scale at 1 (#32).
            let recipes = sent?["recipes"] as? [[String: Any]]
            XCTAssertEqual(recipes?.count, 1)
            XCTAssertEqual(recipes?.first?["scale"] as? Double, 1)
            XCTAssertNil(sent?["recipe_ids"])
            let sides = sent?["loose_ingredients"] as? [[String: Any]]
            XCTAssertEqual(sides?.first?["name"] as? String, "frozen peas")
            XCTAssertEqual(sides?.first?["quantity"] as? Double, 200)
            XCTAssertEqual(sides?.first?["unit"] as? String, "g")
            return (
                200,
                Data(
                    #"{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "name": "Cottage pie", "slot": "dinner", "recipes": [], "loose_ingredients": [], "created_at": "2026-07-25T09:00:00Z"}"#
                    .utf8
                )
            )
        }
        _ = try await client(protocolClass: StubProtocol.self).updateMeal(
            id: UUID(),
            recipeIds: [UUID()],
            looseIngredients: [LooseLine(name: "frozen peas", quantity: 200, unit: "g")]
        )
    }

    func testUpdateMealSendsPerRecipeScales() async throws {
        let curry = UUID()
        let rice = UUID()
        StubProtocol.handler = { request in
            let body = request.httpBody ?? request.streamedBody()
            let sent = try? JSONSerialization.jsonObject(with: body ?? Data()) as? [String: Any]
            let recipes = sent?["recipes"] as? [[String: Any]] ?? []
            let byId = Dictionary(
                uniqueKeysWithValues: recipes.map { ($0["recipe_id"] as? String ?? "", $0["scale"] as? Double ?? 0) }
            )
            // "×2 the curry, ×1 the rice" — one meal, two scales.
            XCTAssertEqual(byId[curry.uuidString.lowercased()], 2)
            XCTAssertEqual(byId[rice.uuidString.lowercased()], 1)
            return (
                200,
                Data(
                    #"{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "name": "Curry night", "slot": "dinner", "recipes": [], "loose_ingredients": [], "created_at": "2026-07-25T09:00:00Z"}"#
                    .utf8
                )
            )
        }
        _ = try await client(protocolClass: StubProtocol.self).updateMeal(
            id: UUID(), recipeIds: [curry, rice], scales: [curry: 2]
        )
    }

    func testUpdateRecipeSendsOnlyChangedFields() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "PATCH")
            let body = request.httpBody ?? request.streamedBody()
            let sent = try? JSONSerialization.jsonObject(with: body ?? Data()) as? [String: Any]
            XCTAssertEqual(sent?["servings"] as? Int, 2)
            // Untouched fields are absent, not null: sending them would mark
            // the recipe edited and trigger a pointless meal resync (#29).
            XCTAssertNil(sent?["title"])
            XCTAssertNil(sent?["ingredients"])
            XCTAssertNil(sent?["instructions"])
            return (
                200,
                Data(
                    #"{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "title": "Cottage Pie", "source_url": null, "servings": 2, "prep_minutes": null, "cook_minutes": null, "image_url": null, "instructions": null, "tags": [], "parse_source": "jsonld", "edited": true, "ingredients": []}"#
                    .utf8
                )
            )
        }
        let updated = try await client(protocolClass: StubProtocol.self).updateRecipe(
            id: UUID(), servings: .some(2)
        )
        XCTAssertEqual(updated.servings, 2)
        XCTAssertTrue(updated.edited)
    }

    /// Clearing the photo has to reach the server as an explicit null —
    /// "no image_url key" means "leave it alone", which is the opposite.
    func testUpdateRecipeSendsExplicitNullToClearThePhoto() async throws {
        StubProtocol.handler = { request in
            let body = request.httpBody ?? request.streamedBody()
            let sent = try? JSONSerialization.jsonObject(with: body ?? Data()) as? [String: Any]
            XCTAssertTrue(sent?.keys.contains("image_url") ?? false)
            XCTAssertTrue(sent?["image_url"] is NSNull)
            return (
                200,
                Data(
                    #"{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "title": "Cottage Pie", "source_url": null, "servings": null, "prep_minutes": null, "cook_minutes": null, "image_url": null, "instructions": null, "tags": [], "parse_source": "jsonld", "edited": true, "ingredients": []}"#
                    .utf8
                )
            )
        }
        let updated = try await client(protocolClass: StubProtocol.self).updateRecipe(
            id: UUID(), imageUrl: .some(nil)
        )
        XCTAssertNil(updated.imageUrl)
    }

    func testUpdateRecipeSendsANewPhotoURL() async throws {
        StubProtocol.handler = { request in
            let body = request.httpBody ?? request.streamedBody()
            let sent = try? JSONSerialization.jsonObject(with: body ?? Data()) as? [String: Any]
            XCTAssertEqual(sent?["image_url"] as? String, "https://example.com/pie.jpg")
            return (
                200,
                Data(
                    #"{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "title": "Cottage Pie", "source_url": null, "servings": null, "prep_minutes": null, "cook_minutes": null, "image_url": "https://example.com/pie.jpg", "instructions": null, "tags": [], "parse_source": "jsonld", "edited": true, "ingredients": []}"#
                    .utf8
                )
            )
        }
        let updated = try await client(protocolClass: StubProtocol.self).updateRecipe(
            id: UUID(), imageUrl: .some("https://example.com/pie.jpg")
        )
        XCTAssertEqual(updated.imageUrl, "https://example.com/pie.jpg")
    }

    func testUpdateRecipeSendsIngredientsWhenTheyChange() async throws {
        StubProtocol.handler = { request in
            let body = request.httpBody ?? request.streamedBody()
            let sent = try? JSONSerialization.jsonObject(with: body ?? Data()) as? [String: Any]
            let lines = sent?["ingredients"] as? [[String: Any]]
            XCTAssertEqual(lines?.count, 1)
            XCTAssertEqual(lines?.first?["name"] as? String, "minced beef")
            XCTAssertEqual(lines?.first?["quantity"] as? Double, 750)
            return (
                200,
                Data(
                    #"{"id": "61931d47-4154-418a-a43f-f734a0e3d888", "title": "Cottage Pie", "source_url": null, "servings": null, "prep_minutes": null, "cook_minutes": null, "image_url": null, "instructions": null, "tags": [], "parse_source": "jsonld", "edited": true, "ingredients": []}"#
                    .utf8
                )
            )
        }
        _ = try await client(protocolClass: StubProtocol.self).updateRecipe(
            id: UUID(), ingredients: [LooseLine(name: "minced beef", quantity: 750, unit: "g")]
        )
    }

    func testDeleteMeal() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "DELETE")
            XCTAssertTrue(request.url!.path.hasPrefix("/meals/"))
            return (204, Data())
        }
        try await client(protocolClass: StubProtocol.self).deleteMeal(id: UUID())
    }

    func testChangePasswordPostsBothFieldsAndReturnsFreshToken() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/auth/password")
            let body = request.httpBody ?? request.streamedBody()
            let sent = try? JSONSerialization.jsonObject(with: body ?? Data()) as? [String: Any]
            XCTAssertEqual(sent?["current_password"] as? String, "a-strong-password")
            XCTAssertEqual(sent?["new_password"] as? String, "an-even-stronger-password")
            return (
                200,
                Data(
                    #"{"token": "meals_fresh-token", "token_type": "bearer", "user": {"id": "61931d47-4154-418a-a43f-f734a0e3d888", "email": "marcus@example.com", "display_name": "Marcus", "created_at": "2026-07-25T09:00:00Z"}}"#
                    .utf8
                )
            )
        }
        let auth = try await client(protocolClass: StubProtocol.self)
            .changePassword(currentPassword: "a-strong-password", newPassword: "an-even-stronger-password")
        XCTAssertEqual(auth.token, "meals_fresh-token")
        XCTAssertEqual(auth.user.displayName, "Marcus")
    }

    func testWrongCurrentPasswordSurfacesServerDetail() async {
        StubProtocol.handler = { _ in (401, Data(#"{"detail": "current password is incorrect"}"#.utf8)) }
        do {
            _ = try await client(protocolClass: StubProtocol.self)
                .changePassword(currentPassword: "nope", newPassword: "an-even-stronger-password")
            XCTFail("expected an error")
        } catch let error as APIError {
            XCTAssertEqual(error, .unauthorized(detail: "current password is incorrect"))
        } catch {
            XCTFail("unexpected error type: \(error)")
        }
    }

    func testTransportErrorMapsToOffline() async {
        do {
            _ = try await client(protocolClass: FailingProtocol.self).fetchAisles()
            XCTFail("expected an error")
        } catch let error as APIError {
            XCTAssertEqual(error, .offline)
        } catch {
            XCTFail("unexpected error type: \(error)")
        }
    }
}

// MARK: - Account lifecycle (decision Q20)

final class AccountLifecycleTests: XCTestCase {
    override func tearDown() {
        StubProtocol.handler = nil
        super.tearDown()
    }

    private func client(protocolClass: AnyClass, token: String? = "meals_test-token") -> APIClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [protocolClass]
        return APIClient(
            baseURL: URL(string: "http://testserver")!,
            token: token,
            session: URLSession(configuration: config)
        )
    }

    private func sentBody(_ request: URLRequest) -> [String: Any]? {
        let body = request.httpBody ?? request.streamedBody()
        return try? JSONSerialization.jsonObject(with: body ?? Data()) as? [String: Any]
    }

    func testRequestPasswordResetPostsTheAddress() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url!.path, "/auth/password-reset")
            return (202, Data(#"{"detail": "if that address has an account, a reset code is on its way"}"#.utf8))
        }
        try await client(protocolClass: StubProtocol.self, token: nil)
            .requestPasswordReset(email: "marcus@example.com")
    }

    func testConfirmPasswordResetReturnsAFreshSession() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.url!.path, "/auth/password/reset-confirm")
            let payload = """
            {"token": "meals_new-session", "token_type": "bearer",
             "user": {"id": "8B2E6E38-6B3E-4F45-9A6E-4C0C2F2D1A11", "email": "marcus@example.com",
                      "display_name": "Marcus", "created_at": "2026-07-25T20:00:00Z",
                      "household_id": "1E4E9C1E-0A8E-4C60-9C2A-8E4E2B7D9C31", "household_name": "Home"}}
            """
            return (200, Data(payload.utf8))
        }
        let auth = try await client(protocolClass: StubProtocol.self, token: nil)
            .confirmPasswordReset(code: "ABCD-EFGH-JKMN", newPassword: "brand-new-password")
        XCTAssertEqual(auth.token, "meals_new-session")
        XCTAssertEqual(auth.user.email, "marcus@example.com")
    }

    func testDeleteAccountSendsPasswordInTheBodyNotTheURL() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "DELETE")
            XCTAssertEqual(request.url!.path, "/auth/me")
            // A password in a query string lands in access logs and proxy caches.
            XCTAssertNil(request.url!.query)
            return (200, Data(#"{"household_deleted": true, "detail": "account deleted"}"#.utf8))
        }
        let result = try await client(protocolClass: StubProtocol.self)
            .deleteAccount(password: "a-strong-password")
        XCTAssertTrue(result.householdDeleted)
    }

    func testDeleteAccountCarriesThePassword() async throws {
        let captured = Captured()
        StubProtocol.handler = { [captured] request in
            let body = request.httpBody ?? request.streamedBody()
            captured.body = try? JSONSerialization.jsonObject(with: body ?? Data()) as? [String: Any]
            return (200, Data(#"{"household_deleted": false, "detail": "account deleted"}"#.utf8))
        }
        _ = try await client(protocolClass: StubProtocol.self).deleteAccount(password: "a-strong-password")
        XCTAssertEqual(captured.body?["password"] as? String, "a-strong-password")
    }

    func testWrongPasswordSurfacesAsUnauthorized() async {
        StubProtocol.handler = { _ in
            (401, Data(#"{"detail": "that password is incorrect, so nothing was deleted"}"#.utf8))
        }
        do {
            _ = try await client(protocolClass: StubProtocol.self).deleteAccount(password: "wrong")
            XCTFail("expected an error")
        } catch let error as APIError {
            XCTAssertEqual(error, .unauthorized(detail: "that password is incorrect, so nothing was deleted"))
        } catch {
            XCTFail("unexpected error type: \(error)")
        }
    }

    /// A reference box so the stub closure can hand a value back.
    private final class Captured: @unchecked Sendable {
        var body: [String: Any]?
    }
}
