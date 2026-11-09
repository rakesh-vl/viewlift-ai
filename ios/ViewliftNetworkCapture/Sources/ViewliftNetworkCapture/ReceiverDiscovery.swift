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

    /// Most VPN addresses kept from earlier advertisements.
    static let maxRememberedVPNHosts = 4

    /// True for a Tailscale-range address (100.64.0.0/10). It stays the same for a given Mac.
    static func isVPN(_ host: String) -> Bool {
        let octets = host.split(separator: ".").compactMap { Int($0) }
        guard octets.count == 4, octets.allSatisfy({ (0...255).contains($0) }) else { return false }
        return octets[0] == 100 && (64...127).contains(octets[1])
    }

    /// An advertisement's own addresses, then the VPN addresses learned earlier that it did not list.
    /// The Mac advertises its Tailscale address only while Tailscale is up. A phone that saw the Mac
    /// once without it must still reach the Mac later from another network.
    static func mergingVPN(into fresh: [String], remembered: [String]) -> [String] {
        var hosts = fresh
        var vpnCount = hosts.filter(isVPN).count
        for host in remembered where isVPN(host) && !hosts.contains(host) {
            guard vpnCount < maxRememberedVPNHosts else { break }
            hosts.append(host)
            vpnCount += 1
        }
        return hosts
    }
}

struct DiscoveredReceiver: Sendable, Equatable {
    let host: String
    let port: Int
    let fingerprint: String
    let debugKey: String
    /// Other addresses for the same dashboard, such as its Tailscale address.
    let alternates: [String]

    /// Primary address first, then each usable alternate once.
    var candidateHosts: [String] {
        var hosts = [host]
        for alternate in alternates where !hosts.contains(alternate) {
            hosts.append(alternate)
        }
        return hosts
    }

    static func parse(
        ip: String,
        portText: String,
        fingerprint: String,
        debugKey: String,
        alternates: String = ""
    ) -> DiscoveredReceiver? {
        guard let port = Int(portText), port > 0 else { return nil }
        guard !fingerprint.isEmpty, !debugKey.isEmpty else { return nil }
        let host = ip.trimmingCharacters(in: .whitespacesAndNewlines)
        guard ReceiverAddress.shouldAdopt(currentHost: nil, discovered: host) else { return nil }
        let extras = alternates
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { ReceiverAddress.shouldAdopt(currentHost: host, discovered: $0) }
        return DiscoveredReceiver(
            host: host,
            port: port,
            fingerprint: fingerprint,
            debugKey: debugKey,
            alternates: extras
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
    private var lastResults: Set<NWBrowser.Result> = []
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

    /// Browsers go stale while the app is suspended or the network changes. Start a fresh one.
    func restart() {
        stop()
        start()
    }

    func stop() {
        lock.lock()
        let active = browser
        browser = nil
        lastResults = []
        resolving = false
        lock.unlock()
        active?.cancel()
    }

    private func accept(_ results: Set<NWBrowser.Result>) {
        lock.lock()
        lastResults = results
        if resolving {
            lock.unlock()
            return
        }
        resolving = !results.isEmpty
        lock.unlock()
        guard !results.isEmpty else { return }
        for result in results {
            if let record = Self.record(from: result) {
                finish(record)
                return
            }
        }
        guard let result = results.first,
              case let .service(name, type, domain, _) = result.endpoint else {
            markIdle()
            scheduleRetry()
            return
        }
        resolveTXT(name: name, type: type, domain: domain)
        armWatchdog()
    }

    private func resolveTXT(name: String, type: String, domain: String) {
        let resolver = ReceiverTXTResolver(name: name, type: type, domain: domain) { [weak self] record in
            self?.finish(record)
        } onFailure: { [weak self] in
            self?.failResolve()
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
            debugKey: txt["key"] ?? "",
            alternates: txt["alt"] ?? ""
        )
    }

    private func markIdle() {
        lock.lock()
        resolving = false
        lock.unlock()
    }

    /// NetService callbacks never arrive when the browse result stays unchanged.
    /// Retry until the TXT record is adopted or the browser stops.
    private func scheduleRetry() {
        lock.lock()
        let alive = browser != nil
        lock.unlock()
        guard alive else { return }
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 2) { [weak self] in
            guard let self else { return }
            self.lock.lock()
            let results = self.lastResults
            self.lock.unlock()
            self.accept(results)
        }
    }

    private func armWatchdog() {
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 6) { [weak self] in
            self?.failResolve()
        }
    }

    private func failResolve() {
        lock.lock()
        let stuck = resolving
        lock.unlock()
        guard stuck else { return }
        markIdle()
        scheduleRetry()
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
        let start = { [weak self] in
            guard let self else { return }
            let serviceType = type.hasSuffix(".") ? type : "\(type)."
            let serviceDomain = domain.hasSuffix(".") ? domain : "\(domain)."
            let resolved = NetService(domain: serviceDomain, type: serviceType, name: name)
            resolved.delegate = self
            self.service = resolved
            resolved.schedule(in: .main, forMode: .common)
            resolved.resolve(withTimeout: 5)
        }
        if Thread.isMainThread {
            start()
        } else {
            DispatchQueue.main.async(execute: start)
        }
    }

    func netServiceDidResolveAddress(_ sender: NetService) {
        let fields = NetService.dictionary(fromTXTRecord: sender.txtRecordData() ?? Data())
        let record = DiscoveredReceiver.parse(
            ip: Self.text("ip", fields),
            portText: Self.text("port", fields),
            fingerprint: Self.text("fp", fields),
            debugKey: Self.text("key", fields),
            alternates: Self.text("alt", fields)
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
