#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation
import ObjectiveC.runtime
import Security

#if canImport(SQLite3)
import SQLite3
#endif

enum StateDebugError: Error {
    case invalidPath
    case payloadTooLarge
    case readOnlyQueryRequired
    case missingFile
}

final class StateDebugService {
    static let maxFileBytes = 512_000
    static let maxRows = 1_000

    func defaults() -> [String: JSONValue] {
        let values = UserDefaults.standard.dictionaryRepresentation()
        return ["values": .object(values.mapValues(Self.jsonValue))]
    }

    func setDefault(key: String, value: JSONValue?) throws -> [String: JSONValue] {
        let clean = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty, clean.count <= 256 else { throw StateDebugError.invalidPath }
        if let value {
            UserDefaults.standard.set(Self.foundationValue(value), forKey: clean)
        } else {
            UserDefaults.standard.removeObject(forKey: clean)
        }
        return ["key": .string(clean), "updated": .bool(true)]
    }

    func keychainMetadata() -> [String: JSONValue] {
        let classes = [
            kSecClassGenericPassword,
            kSecClassInternetPassword,
            kSecClassCertificate,
            kSecClassKey,
            kSecClassIdentity
        ]
        var rows: [JSONValue] = []
        for itemClass in classes {
            let query: [CFString: Any] = [
                kSecClass: itemClass,
                kSecReturnAttributes: true,
                kSecMatchLimit: kSecMatchLimitAll
            ]
            var result: CFTypeRef?
            guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess else {
                continue
            }
            let items = (result as? [[String: Any]]) ?? []
            for item in items.prefix(1_000) {
                rows.append(.object([
                    "class": .string(itemClass as String),
                    "service": .string(String(describing: item[kSecAttrService as String] ?? "")),
                    "account": .string(String(describing: item[kSecAttrAccount as String] ?? "")),
                    "access_group": .string(
                        String(describing: item[kSecAttrAccessGroup as String] ?? "")
                    ),
                    "accessible": .string(
                        String(describing: item[kSecAttrAccessible as String] ?? "")
                    )
                ]))
            }
        }
        return [
            "items": .array(rows),
            "values_exposed": .bool(false)
        ]
    }

    func listFiles(relativePath: String) throws -> [String: JSONValue] {
        let url = try sandboxURL(relativePath)
        let urls = try FileManager.default.contentsOfDirectory(
            at: url,
            includingPropertiesForKeys: [.fileSizeKey, .isDirectoryKey, .contentModificationDateKey],
            options: [.skipsHiddenFiles]
        )
        let rows = urls.prefix(2_000).map { item -> JSONValue in
            let values = try? item.resourceValues(
                forKeys: [.fileSizeKey, .isDirectoryKey, .contentModificationDateKey]
            )
            return .object([
                "name": .string(item.lastPathComponent),
                "path": .string(relativeSandboxPath(item)),
                "bytes": .number(Double(values?.fileSize ?? 0)),
                "directory": .bool(values?.isDirectory ?? false),
                "modified": .number(values?.contentModificationDate?.timeIntervalSince1970 ?? 0)
            ])
        }
        return ["files": .array(rows)]
    }

    func readFile(relativePath: String) throws -> [String: JSONValue] {
        let url = try sandboxURL(relativePath)
        let data = try Data(contentsOf: url, options: [.mappedIfSafe])
        guard data.count <= Self.maxFileBytes else { throw StateDebugError.payloadTooLarge }
        if let text = String(data: data, encoding: .utf8) {
            return [
                "path": .string(relativePath),
                "encoding": .string("utf8"),
                "content": .string(text)
            ]
        }
        return [
            "path": .string(relativePath),
            "encoding": .string("base64"),
            "content": .string(data.base64EncodedString())
        ]
    }

    func writeFile(relativePath: String, content: String, base64: Bool) throws {
        let url = try sandboxURL(relativePath)
        let data = base64 ? Data(base64Encoded: content) : content.data(using: .utf8)
        guard let data, data.count <= Self.maxFileBytes else {
            throw StateDebugError.payloadTooLarge
        }
        try data.write(to: url, options: [.atomic])
    }

    func sqliteSchema(relativePath: String) throws -> [String: JSONValue] {
        try sqlite(relativePath: relativePath, query: """
        SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name LIMIT 1000
        """)
    }

    func sqlite(relativePath: String, query: String) throws -> [String: JSONValue] {
        #if canImport(SQLite3)
        let sql = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let first = sql.split(whereSeparator: \.isWhitespace).first?.uppercased() ?? ""
        guard ["SELECT", "PRAGMA", "WITH", "EXPLAIN"].contains(first),
              !sql.contains(";") else {
            throw StateDebugError.readOnlyQueryRequired
        }
        let url = try sandboxURL(relativePath)
        var database: OpaquePointer?
        guard sqlite3_open_v2(url.path, &database, SQLITE_OPEN_READONLY, nil) == SQLITE_OK,
              let database else {
            throw StateDebugError.missingFile
        }
        defer { sqlite3_close(database) }
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK,
              let statement else {
            throw StateDebugError.readOnlyQueryRequired
        }
        defer { sqlite3_finalize(statement) }
        let columnCount = sqlite3_column_count(statement)
        let columns = (0..<columnCount).map {
            String(cString: sqlite3_column_name(statement, $0))
        }
        var rows: [JSONValue] = []
        while sqlite3_step(statement) == SQLITE_ROW, rows.count < Self.maxRows {
            var row: [String: JSONValue] = [:]
            for index in 0..<columnCount {
                row[columns[Int(index)]] = sqliteValue(statement, index)
            }
            rows.append(.object(row))
        }
        return [
            "columns": .array(columns.map(JSONValue.string)),
            "rows": .array(rows),
            "truncated": .bool(rows.count == Self.maxRows)
        ]
        #else
        return ["error": .string("SQLite3 unavailable")]
        #endif
    }

    func snapshot(paths: [String]) throws -> [String: JSONValue] {
        var files: [String: JSONValue] = [:]
        var total = 0
        for path in paths.prefix(100) {
            let url = try sandboxURL(path)
            let data = try Data(contentsOf: url)
            total += data.count
            guard total <= 2_000_000 else { throw StateDebugError.payloadTooLarge }
            files[path] = .string(data.base64EncodedString())
        }
        return [
            "defaults": defaults()["values"] ?? .object([:]),
            "files": .object(files),
            "created_at": .number(Date().timeIntervalSince1970)
        ]
    }

    func restore(snapshot: [String: JSONValue]) throws {
        if case let .object(values) = snapshot["defaults"] {
            for (key, value) in values {
                UserDefaults.standard.set(Self.foundationValue(value), forKey: key)
            }
        }
        if case let .object(files) = snapshot["files"] {
            for (path, value) in files {
                guard case let .string(encoded) = value,
                      let data = Data(base64Encoded: encoded),
                      data.count <= Self.maxFileBytes else {
                    throw StateDebugError.payloadTooLarge
                }
                try data.write(to: sandboxURL(path), options: [.atomic])
            }
        }
    }

    private func sandboxURL(_ relativePath: String) throws -> URL {
        let root = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
            .standardizedFileURL
        let clean = relativePath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.hasPrefix("/"), !clean.contains("\0") else {
            throw StateDebugError.invalidPath
        }
        let candidate = root.appendingPathComponent(clean).standardizedFileURL
        let rootPath = root.path.hasSuffix("/") ? root.path : root.path + "/"
        guard candidate.path == root.path || candidate.path.hasPrefix(rootPath) else {
            throw StateDebugError.invalidPath
        }
        return candidate
    }

    private func relativeSandboxPath(_ url: URL) -> String {
        let root = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
            .standardizedFileURL.path
        return String(url.standardizedFileURL.path.dropFirst(root.count))
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }

    private static func jsonValue(_ value: Any) -> JSONValue {
        switch value {
        case let value as Bool: return .bool(value)
        case let value as NSNumber: return .number(value.doubleValue)
        case let value as String: return .string(value)
        case let value as Date: return .number(value.timeIntervalSince1970)
        case let value as [Any]: return .array(value.map(jsonValue))
        case let value as [String: Any]: return .object(value.mapValues(jsonValue))
        default: return .string(String(describing: value))
        }
    }

    private static func foundationValue(_ value: JSONValue) -> Any {
        switch value {
        case let .string(item): return item
        case let .number(item): return item
        case let .bool(item): return item
        case let .object(item): return item.mapValues(foundationValue)
        case let .array(item): return item.map(foundationValue)
        case .null: return NSNull()
        }
    }

    #if canImport(SQLite3)
    private func sqliteValue(_ statement: OpaquePointer, _ index: Int32) -> JSONValue {
        switch sqlite3_column_type(statement, index) {
        case SQLITE_INTEGER: return .number(Double(sqlite3_column_int64(statement, index)))
        case SQLITE_FLOAT: return .number(sqlite3_column_double(statement, index))
        case SQLITE_TEXT:
            guard let text = sqlite3_column_text(statement, index) else { return .null }
            return .string(String(cString: text))
        case SQLITE_BLOB:
            let count = Int(sqlite3_column_bytes(statement, index))
            guard let bytes = sqlite3_column_blob(statement, index) else { return .null }
            return .string(Data(bytes: bytes, count: count).base64EncodedString())
        default: return .null
        }
    }
    #endif
}
#endif
