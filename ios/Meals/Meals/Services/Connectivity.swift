import Foundation
import Network

/// When the network comes back. `NWPathMonitor` reports every change of path;
/// this yields only the moments the device goes from no usable path to one,
/// which is when anything queued offline should go (Q11), rather than waiting
/// for the next tap or the next trip to the foreground.
enum Connectivity {
    static func restored() -> AsyncStream<Void> {
        AsyncStream { continuation in
            let monitor = NWPathMonitor()
            let path = PathState()
            monitor.pathUpdateHandler = { update in
                if path.becameSatisfied(update.status == .satisfied) {
                    continuation.yield()
                }
            }
            continuation.onTermination = { _ in monitor.cancel() }
            monitor.start(queue: DispatchQueue(label: "com.marcuslab.meals.connectivity"))
        }
    }

    /// Whether the last path was usable. Only ever touched on the monitor's
    /// own serial queue, which is what makes the unchecked Sendable safe.
    private final class PathState: @unchecked Sendable {
        /// Launch counts as connected: the first sync covers it either way.
        private var satisfied = true

        func becameSatisfied(_ now: Bool) -> Bool {
            defer { satisfied = now }
            return now && !satisfied
        }
    }
}
