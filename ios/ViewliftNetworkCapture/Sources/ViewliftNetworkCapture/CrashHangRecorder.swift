#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Captures hang and crash stacks with class / file / line when symbols allow.
final class CrashHangRecorder: @unchecked Sendable {
    static let shared = CrashHangRecorder()

    static let maxEvents = 80
    static let maxFrames = 64

    private let lock = NSLock()
    private var hangs: [[String: JSONValue]] = []
    private var crashes: [[String: JSONValue]] = []
    private var installed = false
    private var previousExceptionHandler: NSUncaughtExceptionHandler?

    private init() {}

    func install() {
        lock.lock()
        defer { lock.unlock() }
        guard !installed else { return }
        installed = true
        previousExceptionHandler = NSGetUncaughtExceptionHandler()
        NSSetUncaughtExceptionHandler { exception in
            CrashHangRecorder.shared.recordException(exception)
            CrashHangRecorder.shared.previousExceptionHandler?(exception)
        }
    }

    func recordHang(duration: TimeInterval, symbols: [String]) {
        let event: [String: JSONValue] = [
            "id": .string(UUID().uuidString),
            "kind": .string("hang"),
            "timestamp": .number(Date().timeIntervalSince1970),
            "duration_ms": .number(duration * 1_000),
            "thread": .string(Thread.isMainThread ? "main" : Thread.current.name ?? "background"),
            "frames": .array(Self.parseFrames(symbols).map(JSONValue.object))
        ]
        lock.lock()
        hangs.append(event)
        if hangs.count > Self.maxEvents {
            hangs.removeFirst(hangs.count - Self.maxEvents)
        }
        lock.unlock()
    }

    func recordException(_ exception: NSException) {
        let symbols = exception.callStackSymbols
        let event: [String: JSONValue] = [
            "id": .string(UUID().uuidString),
            "kind": .string("exception"),
            "timestamp": .number(Date().timeIntervalSince1970),
            "name": .string(exception.name.rawValue),
            "reason": .string(exception.reason ?? ""),
            "thread": .string(Thread.isMainThread ? "main" : Thread.current.name ?? "background"),
            "frames": .array(Self.parseFrames(symbols).map(JSONValue.object))
        ]
        lock.lock()
        crashes.append(event)
        if crashes.count > Self.maxEvents {
            crashes.removeFirst(crashes.count - Self.maxEvents)
        }
        lock.unlock()
    }

    func hangList() -> [String: JSONValue] {
        lock.lock()
        let copy = hangs
        lock.unlock()
        return [
            "count": .number(Double(copy.count)),
            "events": .array(copy.map(JSONValue.object))
        ]
    }

    func crashList() -> [String: JSONValue] {
        lock.lock()
        let copy = crashes
        lock.unlock()
        return [
            "count": .number(Double(copy.count)),
            "events": .array(copy.map(JSONValue.object)),
            "note": .string(
                "In-process exceptions only. Fatal SIGABRT/SIGSEGV after death need device crash reports."
            )
        ]
    }

    /// Parses `Thread.callStackSymbols` into readable frames.
    /// Example: `2   AppCMS 0x102ab  MyType.method() + 48 (File.swift:123)`
    static func parseFrames(_ symbols: [String]) -> [[String: JSONValue]] {
        symbols.prefix(maxFrames).enumerated().map { index, line in
            var frame: [String: JSONValue] = [
                "index": .number(Double(index)),
                "raw": .string(line)
            ]
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            let parts = trimmed.split(whereSeparator: \.isWhitespace).map(String.init)
            if parts.count >= 4 {
                frame["image"] = .string(parts[1])
                frame["address"] = .string(parts[2])
            }
            if let fileLine = extractFileLine(trimmed) {
                frame["file"] = .string(fileLine.file)
                frame["line"] = .number(Double(fileLine.line))
            }
            if let symbol = extractSymbol(trimmed) {
                frame["symbol"] = .string(symbol)
                if let typeName = symbol.split(separator: ".").first {
                    frame["class"] = .string(String(typeName))
                }
            }
            return frame
        }
    }

    private static func extractFileLine(_ line: String) -> (file: String, line: Int)? {
        guard let open = line.lastIndex(of: "("),
              let close = line.lastIndex(of: ")"),
              open < close else {
            return nil
        }
        let inner = String(line[line.index(after: open)..<close])
        guard let colon = inner.lastIndex(of: ":") else { return nil }
        let file = String(inner[..<colon])
        let lineText = String(inner[inner.index(after: colon)...])
        guard file.hasSuffix(".swift") || file.hasSuffix(".m") || file.hasSuffix(".mm"),
              let number = Int(lineText) else {
            return nil
        }
        return (file, number)
    }

    private static func extractSymbol(_ line: String) -> String? {
        guard let plusRange = line.range(of: " + ") else {
            if let paren = line.lastIndex(of: "(") {
                let before = line[..<paren].trimmingCharacters(in: .whitespaces)
                let tokens = before.split(whereSeparator: \.isWhitespace)
                return tokens.dropFirst(3).joined(separator: " ").nilIfEmpty
            }
            return nil
        }
        let beforePlus = line[..<plusRange.lowerBound]
        let tokens = beforePlus.split(whereSeparator: \.isWhitespace)
        guard tokens.count >= 4 else { return nil }
        return tokens.dropFirst(3).joined(separator: " ").nilIfEmpty
    }
}

private extension String {
    var nilIfEmpty: String? {
        trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : self
    }
}
#endif
