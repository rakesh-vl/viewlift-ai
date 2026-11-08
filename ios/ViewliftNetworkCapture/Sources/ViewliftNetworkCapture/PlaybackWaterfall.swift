#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

struct PlaybackTransfer {
    let url: URL
    let status: Int?
    let bytes: Int
    let durationMs: Double
    let ttfbMs: Double?
    let headers: [String: String]
}

enum PlaybackResourceKind: String {
    case playlist
    case manifest
    case initialization
    case segment
}

enum PlaybackResource {
    /// Playlist, manifest, init segment, or media segment. Query strings are ignored.
    static func kind(for url: URL) -> PlaybackResourceKind? {
        let name = url.lastPathComponent.lowercased()
        if name.hasSuffix(".m3u8") {
            return .playlist
        }
        if name.hasSuffix(".mpd") {
            return .manifest
        }
        if name == "init.mp4" {
            return .initialization
        }
        if name.hasSuffix(".m4s") || name.hasSuffix(".cmfv") || name.hasSuffix(".cmfa") || name.hasSuffix(".ts") {
            return .segment
        }
        return nil
    }

    static func path(for url: URL) -> String {
        let path = url.path
        if path.isEmpty {
            return "/"
        }
        return String(path.prefix(300))
    }
}

/// Metadata for HLS and DASH requests. Bodies, query strings, and tokens are not stored.
final class PlaybackWaterfall: @unchecked Sendable {
    static let shared = PlaybackWaterfall()
    static let maxEntries = 120

    private static let cacheHeaderNames: Set<String> = [
        "cache-control", "age", "expires", "x-cache", "cf-cache-status", "x-cache-status"
    ]

    private let lock = NSLock()
    private var entries: [[String: JSONValue]] = []

    private init() {}

    func record(_ transfer: PlaybackTransfer) {
        guard let kind = PlaybackResource.kind(for: transfer.url) else { return }
        let row = Self.row(transfer, kind: kind)
        lock.lock()
        entries.append(row)
        if entries.count > Self.maxEntries {
            entries.removeFirst(entries.count - Self.maxEntries)
        }
        lock.unlock()
    }

    func snapshot() -> [String: JSONValue] {
        lock.lock()
        let copy = entries
        lock.unlock()
        return [
            "entries": .array(copy.reversed().map(JSONValue.object)),
            "count": .number(Double(copy.count)),
            "note": .string("Host, status, size, and timing only. Query strings and bodies are not stored.")
        ]
    }

    func resetForTests() {
        lock.lock()
        entries = []
        lock.unlock()
    }

    private static func row(_ transfer: PlaybackTransfer, kind: PlaybackResourceKind) -> [String: JSONValue] {
        var row: [String: JSONValue] = [
            "timestamp": .number(Date().timeIntervalSince1970),
            "kind": .string(kind.rawValue),
            "host": .string(transfer.url.host ?? ""),
            "path": .string(PlaybackResource.path(for: transfer.url)),
            "bytes": .number(Double(transfer.bytes)),
            "duration_ms": .number(transfer.durationMs),
            "ttfb_ms": transfer.ttfbMs.map { JSONValue.number($0) } ?? .null,
            "cache": .object(cacheHeaders(transfer.headers))
        ]
        if let status = transfer.status {
            row["status"] = .number(Double(status))
        }
        return row
    }

    private static func cacheHeaders(_ headers: [String: String]) -> [String: JSONValue] {
        var kept: [String: JSONValue] = [:]
        for (key, value) in headers {
            let name = key.lowercased()
            guard cacheHeaderNames.contains(name) else { continue }
            kept[name] = .string(String(value.prefix(180)))
        }
        return kept
    }
}
#endif
