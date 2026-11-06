import Foundation

/// Addresses that can reach the dashboard: the Wi-Fi address first, then VPN alternates such as Tailscale.
struct ReceiverCandidates: Equatable, Sendable {
    private(set) var hosts: [String] = []
    private var index = 0

    var count: Int { hosts.count }

    var current: String? { hosts.indices.contains(index) ? hosts[index] : nil }

    mutating func replace(with newHosts: [String], preferring preferred: String? = nil) {
        var unique: [String] = []
        for host in newHosts where !host.isEmpty && !unique.contains(host) {
            unique.append(host)
        }
        hosts = unique
        index = preferred.flatMap { unique.firstIndex(of: $0) } ?? 0
    }

    mutating func rotate() -> String? {
        guard hosts.count > 1 else { return nil }
        index = (index + 1) % hosts.count
        return hosts[index]
    }
}

/// Tracks connectivity failures so the transport can try the next address and report why it was offline.
struct ReceiverRoute: Equatable, Sendable {
    static let rotateAfterFailures = 2
    static let maxRecordedFailures = 8

    var candidates = ReceiverCandidates()
    private var failures = 0
    private(set) var recentFailures: [String] = []

    mutating func recordSuccess() {
        failures = 0
    }

    /// Returns the next address when repeated failures call for switching.
    mutating func recordFailure(_ summary: String) -> String? {
        recentFailures.append(summary)
        if recentFailures.count > Self.maxRecordedFailures {
            recentFailures.removeFirst(recentFailures.count - Self.maxRecordedFailures)
        }
        failures += 1
        guard failures >= Self.rotateAfterFailures else { return nil }
        failures = 0
        return candidates.rotate()
    }

    mutating func takeFailures() -> [String] {
        defer { recentFailures = [] }
        return recentFailures
    }
}

/// Last dashboard that accepted this app. Lets a relaunch, or a phone away from the Mac's Wi-Fi,
/// reconnect over Tailscale before Bonjour finds anything.
struct RememberedReceiver: Codable, Equatable, Sendable {
    var hosts: [String]
    var port: Int
    var fingerprint: String
    var debugKey: String
}

protocol ReceiverMemoryStoring: Sendable {
    func load() -> RememberedReceiver?
    func save(_ receiver: RememberedReceiver)
}

struct UserDefaultsReceiverMemory: ReceiverMemoryStoring, @unchecked Sendable {
    private static let storageKey = "com.viewlift.networkcapture.receiver"
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func load() -> RememberedReceiver? {
        guard let data = defaults.data(forKey: Self.storageKey) else { return nil }
        return try? JSONDecoder().decode(RememberedReceiver.self, from: data)
    }

    func save(_ receiver: RememberedReceiver) {
        guard let data = try? JSONEncoder().encode(receiver) else { return }
        defaults.set(data, forKey: Self.storageKey)
    }
}

enum TransportFailure {
    /// Network could not reach the receiver. Not a rejected request and not our own cancellation.
    static func isConnectivity(_ error: Error) -> Bool {
        guard let urlError = error as? URLError else { return false }
        switch urlError.code {
        case .timedOut, .cannotConnectToHost, .cannotFindHost, .networkConnectionLost,
             .notConnectedToInternet, .dnsLookupFailed, .dataNotAllowed, .internationalRoamingOff:
            return true
        default:
            return false
        }
    }

    static func summary(path: String, error: Error) -> String {
        let code = (error as NSError).code
        let stamp = ISO8601DateFormatter().string(from: Date())
        return "\(stamp) \(path) error \(code)"
    }
}
