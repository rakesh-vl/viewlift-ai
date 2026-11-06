#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Darwin
import Foundation
import OSLog

/// Copies stdout and stderr into a ring, and reads recent `os_log` lines at hang time.
final class HangConsoleBuffer: @unchecked Sendable {
    static let shared = HangConsoleBuffer()

    private let lock = NSLock()
    private var installed = false
    private var lines: [HangLogLine] = []
    private var partial: [String: String] = [:]
    private var pipes: [Pipe] = []

    private init() {}

    func install() {
        lock.lock()
        if installed {
            lock.unlock()
            return
        }
        installed = true
        lock.unlock()
        LogService.consoleHook = { level, subsystem, message in
            let text = "[\(level)] \(subsystem): \(message)"
            HangConsoleBuffer.shared.append(source: "sdk", message: text)
        }
        tee(STDOUT_FILENO, source: "stdout")
        tee(STDERR_FILENO, source: "stderr")
    }

    func append(source: String, message: String) {
        let clean = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        lock.lock()
        lines.append(HangLogLine(
            time: Date().timeIntervalSince1970,
            source: source,
            message: String(clean.prefix(180))
        ))
        if lines.count > 80 {
            lines.removeFirst(lines.count - 80)
        }
        lock.unlock()
    }

    func recent(before date: Date, seconds: TimeInterval, limit: Int = 30) -> [JSONValue] {
        let start = date.timeIntervalSince1970 - seconds
        lock.lock()
        var copy = lines.filter { $0.time >= start }
        for (source, pending) in partial where !pending.isEmpty {
            copy.append(HangLogLine(
                time: date.timeIntervalSince1970,
                source: source,
                message: String(pending.prefix(180))
            ))
        }
        lock.unlock()
        var rows = copy
        for entry in systemLog(since: date.addingTimeInterval(-seconds)) {
            if rows.contains(where: { $0.message == entry.message }) {
                continue
            }
            rows.append(entry)
        }
        rows.sort { $0.time < $1.time }
        return rows.suffix(limit).map { line in
            .object([
                "source": .string(line.source),
                "message": .string(line.message),
                "ago_ms": .number(max(0, (date.timeIntervalSince1970 - line.time) * 1_000))
            ])
        }
    }

    private func tee(_ fd: Int32, source: String) {
        let original = dup(fd)
        guard original >= 0 else { return }
        let pipe = Pipe()
        guard dup2(pipe.fileHandleForWriting.fileDescriptor, fd) >= 0 else {
            close(original)
            return
        }
        pipes.append(pipe)
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self?.consume(data, source: source, original: original)
        }
    }

    private func consume(_ data: Data, source: String, original: Int32) {
        writeBack(data, fd: original)
        guard let text = String(data: data, encoding: .utf8) else { return }
        lock.lock()
        var pending = partial[source] ?? ""
        pending.append(text)
        let pieces = pending.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        partial[source] = pieces.last
        lock.unlock()
        for piece in pieces.dropLast() {
            append(source: source, message: piece)
        }
    }

    private func writeBack(_ data: Data, fd: Int32) {
        data.withUnsafeBytes { raw in
            guard let base = raw.baseAddress else { return }
            var sent = 0
            while sent < data.count {
                let wrote = write(fd, base.advanced(by: sent), data.count - sent)
                if wrote <= 0 { break }
                sent += wrote
            }
        }
    }

    private func systemLog(since date: Date) -> [HangLogLine] {
        guard let store = try? OSLogStore(scope: .currentProcessIdentifier) else { return [] }
        guard let entries = try? store.getEntries(at: store.position(date: date)) else { return [] }
        var rows: [HangLogLine] = []
        for entry in entries {
            guard let log = entry as? OSLogEntryLog else { continue }
            let message = "[\(levelName(log.level))] \(log.subsystem) \(log.category): \(log.composedMessage)"
            rows.append(HangLogLine(
                time: log.date.timeIntervalSince1970,
                source: "oslog",
                message: String(message.prefix(180))
            ))
            if rows.count > 80 {
                rows.removeFirst(rows.count - 80)
            }
        }
        return rows
    }

    private func levelName(_ level: OSLogEntryLog.Level) -> String {
        switch level {
        case .debug: return "debug"
        case .info: return "info"
        case .notice: return "notice"
        case .error: return "error"
        case .fault: return "fault"
        default: return "log"
        }
    }
}

private struct HangLogLine {
    var time: TimeInterval
    var source: String
    var message: String
}
#endif
