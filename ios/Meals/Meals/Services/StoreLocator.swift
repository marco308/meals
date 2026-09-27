import CoreLocation

/// Which saved store the phone is standing in (decision Q25). Supermarkets
/// carry where the *store* is; the phone's own position is compared with them
/// here and goes no further — nothing about it is ever sent to the server.
enum StoreMatcher {
    /// A fix vaguer than this can't tell a supermarket from the one across
    /// the road, so it says nothing either way.
    static let worstUsableAccuracy: CLLocationDistance = 200
    /// The server fills in its default whenever a store has a location; this
    /// only covers a reply that somehow didn't.
    static let fallbackRadius = 150

    static func isUsable(_ location: CLLocation) -> Bool {
        location.horizontalAccuracy >= 0 && location.horizontalAccuracy <= worstUsableAccuracy
    }

    /// The nearest store whose radius takes in `location`, or nil when none
    /// does (or the fix is too vague to say).
    static func store(at location: CLLocation, in markets: [Supermarket]) -> Supermarket? {
        guard isUsable(location) else { return nil }
        var best: (market: Supermarket, distance: CLLocationDistance)?
        for market in markets {
            guard let latitude = market.latitude, let longitude = market.longitude else { continue }
            let distance = location.distance(from: CLLocation(latitude: latitude, longitude: longitude))
            guard distance <= Double(market.radiusM ?? fallbackRadius) else { continue }
            if best == nil || distance < best!.distance {
                best = (market, distance)
            }
        }
        return best?.market
    }
}

/// One position, when it's wanted: "While Using" permission only, no
/// background tracking, no region monitoring. A shop is walked with the list
/// open, so that is the moment worth checking.
@MainActor
final class StoreLocator {
    /// How long to wait for a usable fix, a permission prompt included.
    private let patience: Duration = .seconds(30)

    /// The phone's position, or nil when it can't or mayn't say. Asks for
    /// permission only when `mayAsk`: the caller decides when a prompt makes
    /// sense.
    func currentLocation(mayAsk: Bool) async -> CLLocation? {
        switch CLLocationManager().authorizationStatus {
        case .denied, .restricted:
            return nil
        case .notDetermined where !mayAsk:
            return nil
        default:
            break
        }
        let session = CLServiceSession(authorization: .whenInUse)
        defer { session.invalidate() }
        let patience = self.patience
        return await withTaskGroup(of: CLLocation?.self) { group in
            group.addTask { await Self.firstUsableFix() }
            group.addTask {
                try? await Task.sleep(for: patience)
                return nil
            }
            let first = await group.next() ?? nil
            group.cancelAll()
            return first
        }
    }

    private nonisolated static func firstUsableFix() async -> CLLocation? {
        do {
            for try await update in CLLocationUpdate.liveUpdates() {
                if update.authorizationDenied || update.authorizationDeniedGlobally || update.authorizationRestricted {
                    return nil
                }
                if let location = update.location, StoreMatcher.isUsable(location) {
                    return location
                }
            }
        } catch {
            return nil
        }
        return nil
    }
}
