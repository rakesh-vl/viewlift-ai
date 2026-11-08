#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// What a sandbox file looks like, so the dashboard can pick a preview.
enum StorageFileKind: String {
    case image, plist, json, sqlite, text, binary

    static func detect(_ data: Data) -> StorageFileKind {
        if data.starts(with: Data("SQLite format 3\u{0}".utf8)) { return .sqlite }
        if data.starts(with: Data("bplist".utf8)) { return .plist }
        if isImage(data) { return .image }
        guard let text = String(data: data.prefix(4_096), encoding: .utf8) else { return .binary }
        let head = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if head.hasPrefix("<?xml"), head.contains("<plist") || head.contains("<!DOCTYPE plist") {
            return .plist
        }
        if head.hasPrefix("{") || head.hasPrefix("["),
           (try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])) != nil {
            return .json
        }
        return .text
    }

    /// Binary and XML plists decoded for display; nil for anything else.
    static func plistJSON(_ data: Data) -> JSONValue? {
        guard detect(data) == .plist,
              let value = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil) else {
            return nil
        }
        return StateDebugService.jsonValue(value)
    }

    private static func isImage(_ data: Data) -> Bool {
        let signatures: [[UInt8]] = [
            [0x89, 0x50, 0x4E, 0x47], [0xFF, 0xD8, 0xFF], [0x47, 0x49, 0x46, 0x38], [0x42, 0x4D]
        ]
        if signatures.contains(where: { data.starts(with: $0) }) { return true }
        guard data.count >= 12, data.starts(with: Data("RIFF".utf8)) else { return false }
        // Offsets from `startIndex`, so a Data slice does not index out of range.
        return String(bytes: data.dropFirst(8).prefix(4), encoding: .utf8) == "WEBP"
    }
}

/// Defaults, file, and cookie edits for the storage panel.
extension StateDebugService {
    /// Top-level sandbox containers stay; deleting them breaks the app until reinstall.
    private static let protectedPaths: Set<String> = ["", "Documents", "Library", "tmp", "SystemData",
                                                      "Library/Caches", "Library/Preferences"]

    func deleteDefault(key: String) throws -> [String: JSONValue] {
        var out = try setDefault(key: key, value: nil)
        out["deleted"] = .bool(true)
        return out
    }

    func renameFile(relativePath: String, newName: String) throws -> [String: JSONValue] {
        let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name != ".", name != "..", !name.contains("/"), !name.contains("\0") else {
            throw StateDebugError.invalidPath
        }
        let source = try sandboxURL(relativePath)
        try guardMutable(source)
        let target = try sandboxURL(
            (relativeSandboxPath(source.deletingLastPathComponent()) as NSString).appendingPathComponent(name)
        )
        guard !FileManager.default.fileExists(atPath: target.path) else { throw StateDebugError.alreadyExists }
        try FileManager.default.moveItem(at: source, to: target)
        return ["path": .string(relativeSandboxPath(target)), "renamed": .bool(true)]
    }

    func deleteFile(relativePath: String) throws -> [String: JSONValue] {
        let url = try sandboxURL(relativePath)
        try guardMutable(url)
        try FileManager.default.removeItem(at: url)
        return ["path": .string(relativeSandboxPath(url)), "deleted": .bool(true)]
    }

    /// Cookie values are masked; names, scope, and flags are enough to debug session issues.
    func cookies(storage: HTTPCookieStorage = .shared) -> [String: JSONValue] {
        let rows = (storage.cookies ?? []).sorted { ($0.domain, $0.name) < ($1.domain, $1.name) }.map { cookie in
            JSONValue.object([
                "name": .string(cookie.name),
                "domain": .string(cookie.domain),
                "path": .string(cookie.path),
                "value_preview": .string(Self.mask(cookie.value)),
                "value_length": .number(Double(cookie.value.count)),
                "expires": cookie.expiresDate.map { .number($0.timeIntervalSince1970) } ?? .null,
                "session_only": .bool(cookie.isSessionOnly),
                "secure": .bool(cookie.isSecure),
                "http_only": .bool(cookie.isHTTPOnly),
                "same_site": .string(cookie.sameSitePolicy?.rawValue ?? "")
            ])
        }
        return ["cookies": .array(rows), "count": .number(Double(rows.count)), "values_exposed": .bool(false)]
    }

    func deleteCookie(
        name: String,
        domain: String,
        path: String,
        storage: HTTPCookieStorage = .shared
    ) -> [String: JSONValue] {
        let matches = (storage.cookies ?? []).filter {
            $0.name == name && $0.domain == domain && (path.isEmpty || $0.path == path)
        }
        matches.forEach(storage.deleteCookie)
        return ["deleted": .number(Double(matches.count))]
    }

    private func guardMutable(_ url: URL) throws {
        guard !Self.protectedPaths.contains(relativeSandboxPath(url)) else { throw StateDebugError.protectedPath }
        guard FileManager.default.fileExists(atPath: url.path) else { throw StateDebugError.missingFile }
    }

    static func mask(_ value: String) -> String {
        guard value.count > 8 else { return String(repeating: "•", count: value.count) }
        return "\(value.prefix(4))…\(value.suffix(2))"
    }
}
#endif
