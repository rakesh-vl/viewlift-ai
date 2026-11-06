#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// Captures hang and crash stacks with class / file / line when symbols allow.
final class CrashHangRecorder: @unchecked Sendable {
    static let shared = CrashHangRecorder()

    static let maxEvents = 80
    static let maxHangs = 12
    static let maxFrames = 64
    /// Base64 budget for every hang screenshot in one `hang.list` reply.
    static let screenshotBudget = 220_000

    private let lock = NSLock()
    private var hangs: [[String: JSONValue]] = []
    private var crashes: [[String: JSONValue]] = []
    private var installed = false
    private var previousExceptionHandler: NSUncaughtExceptionHandler?

    private init() {}

    func install() {
        lock.lock()
        if installed {
            lock.unlock()
            return
        }
        installed = true
        previousExceptionHandler = NSGetUncaughtExceptionHandler()
        NSSetUncaughtExceptionHandler { exception in
            CrashHangRecorder.shared.recordException(exception)
            CrashHangRecorder.shared.previousExceptionHandler?(exception)
        }
        lock.unlock()
        HangInteractionTrail.shared.install()
        HangConsoleBuffer.shared.install()
    }

    func storeHang(_ event: [String: JSONValue]) {
        lock.lock()
        hangs.append(event)
        if hangs.count > Self.maxHangs {
            hangs.removeFirst(hangs.count - Self.maxHangs)
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
        var copy = hangs
        lock.unlock()
        HangReport.dropOldScreenshots(&copy, budget: Self.screenshotBudget)
        return [
            "count": .number(Double(copy.count)),
            "events": .array(copy.map(JSONValue.object)),
            "note": .string(
                "Each hang includes the screen, a screenshot from when the UI recovered, "
                    + "taps and scrolls before the stall, and console lines. "
                    + "The stack is sampled while the main thread is stuck when the device allows it."
            )
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
                if let typeName = typeName(in: symbol) {
                    frame["class"] = .string(typeName)
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

    static func typeName(in symbol: String) -> String? {
        var text = symbol
        if let marker = text.range(of: " in ") {
            text = String(text[marker.upperBound...])
        }
        let head = text.split(separator: "(", maxSplits: 1).first.map(String.init) ?? text
        let parts = head.split(separator: ".").map(String.init).filter { !$0.isEmpty }
        guard parts.count >= 2 else { return nil }
        return parts[parts.count - 2]
    }

    private static func extractSymbol(_ line: String) -> String? {
        if let plusRange = line.range(of: " + ") {
            return tokens(in: line[..<plusRange.lowerBound])
        }
        if let paren = line.lastIndex(of: "("),
           line.contains(".swift:") || line.contains(".m:") || line.contains(".mm:") {
            return tokens(in: line[..<paren])
        }
        return tokens(in: Substring(line))
    }

    private static func tokens(in text: Substring) -> String? {
        let parts = text.split(whereSeparator: \.isWhitespace)
        guard parts.count >= 4 else { return nil }
        return parts.dropFirst(3).joined(separator: " ").nilIfEmpty
    }
}

private extension String {
    var nilIfEmpty: String? {
        trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : self
    }
}
#endif
