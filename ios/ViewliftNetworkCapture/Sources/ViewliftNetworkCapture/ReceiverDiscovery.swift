import Foundation

enum ReceiverAddress {
    static func shouldAdopt(currentHost: String?, discovered: String) -> Bool {
        let host = discovered.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !host.isEmpty, host != currentHost else { return false }
        if host == "0.0.0.0" || host.hasPrefix("169.254.") {
            return false
        }
        #if targetEnvironment(simulator)
        return true
        #else
        return host != "127.0.0.1"
        #endif
    }
}

struct DiscoveredReceiver: Sendable, Equatable {
    let host: String
    let port: Int
    let fingerprint: String
    let debugKey: String

    static func parse(
        ip: String,
        portText: String,
        fingerprint: String,
        debugKey: String
    ) -> DiscoveredReceiver? {
        guard let port = Int(portText), port > 0 else { return nil }
        guard !fingerprint.isEmpty, !debugKey.isEmpty else { return nil }
        let host = ip.trimmingCharacters(in: .whitespacesAndNewlines)
        guard ReceiverAddress.shouldAdopt(currentHost: nil, discovered: host) else { return nil }
        return DiscoveredReceiver(
            host: host,
            port: port,
            fingerprint: fingerprint,
            debugKey: debugKey
        )
    }
}

#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Network

/// Finds the dashboard after the Mac changes Wi-Fi address.
final class ReceiverDiscovery: @unchecked Sendable {
    static let serviceType = "_viewliftdebug._tcp"

    private let lock = NSLock()
    private var browser: NWBrowser?
    private var resolving = false
    private let onFound: @Sendable (DiscoveredReceiver) -> Void
    private var resolver: ReceiverTXTResolver?

    init(onFound: @escaping @Sendable (DiscoveredReceiver) -> Void) {
        self.onFound = onFound
    }

    func start() {
        let parameters = NWParameters.tcp
        parameters.includePeerToPeer = true
        let browser = NWBrowser(
            for: .bonjour(type: Self.serviceType, domain: nil),
            using: parameters
        )
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            self?.accept(results)
        }
        browser.start(queue: .global(qos: .utility))
        lock.lock()
        self.browser = browser
        lock.unlock()
    }

    func stop() {
        lock.lock()
        let active = browser
        browser = nil
        lock.unlock()
        active?.cancel()
    }

    private func accept(_ results: Set<NWBrowser.Result>) {
        guard let result = results.first else { return }
        lock.lock()
        if resolving {
            lock.unlock()
            return
        }
        resolving = true
        lock.unlock()
        if let record = Self.record(from: result) {
            finish(record)
            return
        }
        guard case let .service(name, type, domain, _) = result.endpoint else {
            markIdle()
            return
        }
        resolveTXT(name: name, type: type, domain: domain)
    }

    private func resolveTXT(name: String, type: String, domain: String) {
        let resolver = ReceiverTXTResolver(name: name, type: type, domain: domain) { [weak self] record in
            self?.finish(record)
        } onFailure: { [weak self] in
            self?.markIdle()
        }
        lock.lock()
        self.resolver = resolver
        lock.unlock()
    }

    private func finish(_ record: DiscoveredReceiver) {
        onFound(record)
        markIdle()
    }

    private static func record(from result: NWBrowser.Result) -> DiscoveredReceiver? {
        guard case let .bonjour(txt) = result.metadata else { return nil }
        return DiscoveredReceiver.parse(
            ip: txt["ip"] ?? "",
            portText: txt["port"] ?? "",
            fingerprint: txt["fp"] ?? "",
            debugKey: txt["key"] ?? ""
        )
    }

    private func markIdle() {
        lock.lock()
        resolving = false
        lock.unlock()
    }
}

final class ReceiverTXTResolver: NSObject, NetServiceDelegate {
    private var service: NetService?
    private let onRecord: @Sendable (DiscoveredReceiver) -> Void
    private let onFailure: @Sendable () -> Void

    init(
        name: String,
        type: String,
        domain: String,
        onRecord: @escaping @Sendable (DiscoveredReceiver) -> Void,
        onFailure: @escaping @Sendable () -> Void
    ) {
        self.onRecord = onRecord
        self.onFailure = onFailure
        super.init()
        let serviceType = type.hasSuffix(".") ? type : "\(type)."
        let serviceDomain = domain.hasSuffix(".") ? domain : "\(domain)."
        let resolved = NetService(domain: serviceDomain, type: serviceType, name: name)
        resolved.delegate = self
        service = resolved
        DispatchQueue.main.async {
            resolved.schedule(in: .main, forMode: .common)
            resolved.resolve(withTimeout: 5)
        }
    }

    func netServiceDidResolveAddress(_ sender: NetService) {
        let fields = NetService.dictionary(fromTXTRecord: sender.txtRecordData() ?? Data())
        let record = DiscoveredReceiver.parse(
            ip: Self.text("ip", fields),
            portText: Self.text("port", fields),
            fingerprint: Self.text("fp", fields),
            debugKey: Self.text("key", fields)
        )
        sender.stop()
        if let record {
            onRecord(record)
        } else {
            onFailure()
        }
    }

    func netService(_ sender: NetService, didNotResolve _: [String: NSNumber]) {
        sender.stop()
        onFailure()
    }

    private static func text(_ key: String, _ fields: [String: Data]) -> String {
        guard let data = fields[key] else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }
}
#endif
