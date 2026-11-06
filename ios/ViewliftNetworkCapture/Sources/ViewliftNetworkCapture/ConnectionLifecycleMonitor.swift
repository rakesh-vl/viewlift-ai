#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import Network
import OSLog

#if canImport(UIKit)
import UIKit
#endif

enum ConnectionLifecycleEvent: Sendable, Equatable {
    case foreground
    case background
    case networkChanged
}

/// Reports app foreground/background and network path changes so the debug bridge can
/// reconnect at once. iOS suspends sockets while the app is in the background.
final class ConnectionLifecycleMonitor: @unchecked Sendable {
    private static let log = Logger(subsystem: "com.viewlift.networkcapture", category: "lifecycle")

    private let lock = NSLock()
    private let handler: @Sendable (ConnectionLifecycleEvent) -> Void
    private var observers: [NSObjectProtocol] = []
    private var pathMonitor: NWPathMonitor?
    private var lastPathSignature: String?
    private var inBackground = false

    init(handler: @escaping @Sendable (ConnectionLifecycleEvent) -> Void) {
        self.handler = handler
    }

    func start() {
        observeApplication()
        observeNetworkPath()
    }

    func stop() {
        lock.lock()
        let tokens = observers
        observers = []
        let monitor = pathMonitor
        pathMonitor = nil
        lastPathSignature = nil
        lock.unlock()
        tokens.forEach { NotificationCenter.default.removeObserver($0) }
        monitor?.cancel()
    }

    private func observeApplication() {
        #if canImport(UIKit)
        let center = NotificationCenter.default
        let background = center.addObserver(
            forName: UIApplication.didEnterBackgroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.setBackground(true)
            self?.handler(.background)
        }
        let active = center.addObserver(
            forName: UIApplication.didBecomeActiveNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            // The first activation is the cold launch. Only a return from background needs a reconnect.
            guard let self, self.setBackground(false) else { return }
            self.handler(.foreground)
        }
        lock.lock()
        observers.append(contentsOf: [background, active])
        lock.unlock()
        #endif
    }

    /// Stores the new state and returns whether it was previously in the background.
    @discardableResult
    private func setBackground(_ value: Bool) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let previous = inBackground
        inBackground = value
        return previous
    }

    private func observeNetworkPath() {
        let monitor = NWPathMonitor()
        monitor.pathUpdateHandler = { [weak self] path in
            self?.accept(path)
        }
        lock.lock()
        pathMonitor = monitor
        lock.unlock()
        monitor.start(queue: DispatchQueue(label: "com.viewlift.networkcapture.path", qos: .utility))
    }

    private func accept(_ path: NWPath) {
        let signature = Self.signature(of: path)
        lock.lock()
        let previous = lastPathSignature
        lastPathSignature = signature
        let suspended = inBackground
        lock.unlock()
        // The first callback only reports the current path. Skip changes while suspended: the
        // foreground event reconnects anyway.
        guard let previous, previous != signature, path.status == .satisfied, !suspended else { return }
        Self.log.info("Network path changed \(signature, privacy: .public)")
        handler(.networkChanged)
    }

    /// Interface set plus reachability. Wi-Fi switch, cellular handoff, or Tailscale coming up all change it.
    static func signature(of path: NWPath) -> String {
        let interfaces = path.availableInterfaces.map { "\($0.type):\($0.name)" }.sorted()
        return "\(path.status)|\(interfaces.joined(separator: ","))"
    }
}

#if os(iOS)
/// Keeps the process alive for a few seconds so the "background" report can leave the device.
final class BackgroundGrace: @unchecked Sendable {
    private let lock = NSLock()
    private var identifier = UIBackgroundTaskIdentifier.invalid

    func begin() async {
        let value = await MainActor.run {
            UIApplication.shared.beginBackgroundTask(withName: "viewlift.lifecycle") { [weak self] in
                self?.end()
            }
        }
        store(value)
    }

    private func store(_ value: UIBackgroundTaskIdentifier) {
        lock.lock()
        identifier = value
        lock.unlock()
    }

    func end() {
        lock.lock()
        let value = identifier
        identifier = .invalid
        lock.unlock()
        guard value != .invalid else { return }
        Task { @MainActor in
            UIApplication.shared.endBackgroundTask(value)
        }
    }
}
#endif
#endif
