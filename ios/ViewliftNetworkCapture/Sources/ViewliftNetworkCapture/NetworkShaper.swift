import Foundation

/// Network conditions for this app's own URLSession traffic, applied in `CaptureURLProtocol`.
/// Only requests the SDK intercepts are shaped, so network capture must be on.
struct NetworkConditions: Equatable, Sendable {
    var latency: TimeInterval
    var lossPercent: Double
    var downloadBytesPerSecond: Double?
    var offline: Bool

    /// Approximate Network Link Conditioner presets (round-trip latency, downlink).
    static let presets: [String: NetworkConditions] = [
        "wifi": online(latency: 0.04, loss: 0, rate: 5_000_000),
        "4g": online(latency: 0.08, loss: 0, rate: 1_500_000),
        "3g": online(latency: 0.2, loss: 0, rate: 97_500),
        "edge": online(latency: 0.4, loss: 0, rate: 30_000),
        "gprs": online(latency: 0.5, loss: 1, rate: 6_250),
        "offline": NetworkConditions(latency: 0, lossPercent: 0, downloadBytesPerSecond: nil, offline: true)
    ]

    private static func online(latency: TimeInterval, loss: Double, rate: Double) -> NetworkConditions {
        NetworkConditions(latency: latency, lossPercent: loss, downloadBytesPerSecond: rate, offline: false)
    }
}

enum NetworkShapingError: Error, CustomStringConvertible {
    case unknownProfile(String)

    var description: String {
        switch self {
        case let .unknownProfile(name):
            return "Unknown profile \(name). Use wifi, 4g, 3g, edge, gprs, offline, or off."
        }
    }
}

/// What `CaptureURLProtocol` does with one request under the current conditions.
enum NetworkShapingDecision: Equatable {
    case pass
    case delay(TimeInterval)
    case fail(URLError.Code)
}

final class NetworkShaper: @unchecked Sendable {
    static let shared = NetworkShaper()

    private let lock = NSLock()
    private var active: (name: String, conditions: NetworkConditions)?
    private let random: () -> Double

    init(random: @escaping () -> Double = { Double.random(in: 0..<100) }) {
        self.random = random
    }

    var conditions: NetworkConditions? {
        lock.lock()
        defer { lock.unlock() }
        return active?.conditions
    }

    /// `net.profile` payload: profile, optional latency_ms and loss_percent overrides.
    func apply(_ payload: [String: JSONValue]) throws -> [String: JSONValue] {
        let name = Self.text(payload["profile"]).lowercased()
        if name.isEmpty || name == "off" {
            lock.lock()
            active = nil
            lock.unlock()
            return ["profile": .string("off"), "capturing": .bool(CaptureRuntime.isCapturingNetwork)]
        }
        guard var conditions = NetworkConditions.presets[name] else {
            throw NetworkShapingError.unknownProfile(name)
        }
        if case let .number(latency) = payload["latency_ms"] {
            conditions.latency = min(max(latency, 0), 10_000) / 1_000
        }
        if case let .number(loss) = payload["loss_percent"] {
            conditions.lossPercent = min(max(loss, 0), 100)
        }
        lock.lock()
        active = (name, conditions)
        lock.unlock()
        return [
            "profile": .string(name),
            "latency_ms": .number((conditions.latency * 1_000).rounded()),
            "loss_percent": .number(conditions.lossPercent),
            "offline": .bool(conditions.offline),
            "capturing": .bool(CaptureRuntime.isCapturingNetwork)
        ]
    }

    func decision() -> NetworkShapingDecision {
        guard let conditions else { return .pass }
        if conditions.offline {
            return .fail(.notConnectedToInternet)
        }
        if conditions.lossPercent > 0, random() < conditions.lossPercent {
            return .fail(.networkConnectionLost)
        }
        return conditions.latency > 0 ? .delay(conditions.latency) : .pass
    }

    /// Extra wait before handing a downloaded body to the app, to match the downlink rate.
    func transferDelay(bytes: Int) -> TimeInterval {
        guard let rate = conditions?.downloadBytesPerSecond, rate > 0, bytes > 0 else { return 0 }
        return min(Double(bytes) / rate, 60)
    }

    private static func text(_ value: JSONValue?) -> String {
        if case let .string(item) = value {
            return item.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return ""
    }
}
