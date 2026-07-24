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
