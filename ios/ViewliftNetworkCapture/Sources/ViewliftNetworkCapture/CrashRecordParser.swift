#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

/// One loaded image listed in a fatal signal record.
struct CrashImage: Equatable {
    let base: UInt64
    let end: UInt64
    let uuid: String
    let path: String

    var name: String {
        path == "?" ? "" : (path as NSString).lastPathComponent
    }

    func contains(_ address: UInt64) -> Bool {
        address >= base && address < end
    }
}

/// A fatal signal record written by the C handler, before it becomes a dashboard event.
struct CrashRecord: Equatable {
    var signalNumber = 0
    var code = 0
    var faultAddress: UInt64 = 0
    var timestamp: TimeInterval = 0
    var processId = 0
    var arch = ""
    var threadId: UInt64 = 0
    var isMainThread = false
    var frames: [UInt64] = []
    var images: [CrashImage] = []
}

/// Reads the plain text record from `ViewliftCrashSignals.c`. A truncated record still yields
/// whatever frames were written, because a crash inside the handler is possible.
enum CrashRecordParser {
    static func parse(_ text: String) -> CrashRecord? {
        var lines = text.split(separator: "\n", omittingEmptySubsequences: true).makeIterator()
        guard lines.next() == "VLC1" else { return nil }
        var record = CrashRecord()
        var sawSignal = false
        while let line = lines.next() {
            let parts = line.split(separator: " ", maxSplits: 1).map(String.init)
            guard let key = parts.first else { continue }
            let rest = parts.count > 1 ? parts[1] : ""
            if key == "sig" {
                sawSignal = readSignal(rest, into: &record)
            } else {
                readField(key, rest, into: &record)
            }
        }
        return sawSignal ? record : nil
    }

    private static func readField(_ key: String, _ rest: String, into record: inout CrashRecord) {
        switch key {
        case "time":
            record.timestamp = TimeInterval(rest) ?? 0
        case "pid":
            record.processId = Int(rest) ?? 0
        case "arch":
            record.arch = rest
        case "thread":
            readThread(rest, into: &record)
        case "frame":
            if let address = hex(rest) { record.frames.append(address) }
        case "image":
            if let image = readImage(rest) { record.images.append(image) }
        default:
            break
        }
    }

    static func hex(_ text: String) -> UInt64? {
        let trimmed = text.hasPrefix("0x") ? String(text.dropFirst(2)) : text
        return UInt64(trimmed, radix: 16)
    }

    /// `3b2c...` (32 hex digits) to `3B2C...-....` form that `dwarfdump` and `.ips` use.
    static func formattedUUID(_ hexText: String) -> String {
        let upper = hexText.uppercased()
        guard upper.count == 32 else { return upper }
        let chars = Array(upper)
        let groups = [0..<8, 8..<12, 12..<16, 16..<20, 20..<32]
        return groups.map { String(chars[$0]) }.joined(separator: "-")
    }

    private static func readSignal(_ text: String, into record: inout CrashRecord) -> Bool {
        let parts = text.split(separator: " ").map(String.init)
        guard parts.count == 3, let number = Int(parts[0]) else { return false }
        record.signalNumber = number
        record.code = Int(parts[1]) ?? 0
        record.faultAddress = hex(parts[2]) ?? 0
        return true
    }

    private static func readThread(_ text: String, into record: inout CrashRecord) {
        let parts = text.split(separator: " ").map(String.init)
        guard parts.count == 2 else { return }
        record.threadId = UInt64(parts[0]) ?? 0
        record.isMainThread = parts[1] == "1"
    }

    private static func readImage(_ text: String) -> CrashImage? {
        let parts = text.split(separator: " ", maxSplits: 3).map(String.init)
        guard parts.count == 4, let base = hex(parts[0]), let end = hex(parts[1]) else { return nil }
        return CrashImage(base: base, end: end, uuid: formattedUUID(parts[2]), path: parts[3])
    }
}

/// Turns a parsed record into the `crash` / `fatal` event payload the dashboard renders.
enum CrashEventBuilder {
    private static let signalNames: [Int: (name: String, summary: String)] = [
        4: ("SIGILL", "Illegal instruction. Usually a Swift runtime trap."),
        5: ("SIGTRAP", "Breakpoint trap. A Swift runtime failure, failed precondition, or force unwrap."),
        6: ("SIGABRT", "Abort. An assertion, fatalError, or an uncaught exception."),
        8: ("SIGFPE", "Arithmetic fault."),
        10: ("SIGBUS", "Bad memory access."),
        11: ("SIGSEGV", "Invalid memory access. A dangling or nil pointer."),
        12: ("SIGSYS", "Bad system call.")
    ]

    static func event(
        from record: CrashRecord,
        executable: String,
        id: String = UUID().uuidString,
        previousSession: Bool = true
    ) -> [String: JSONValue] {
        let signal = signalNames[record.signalNumber] ?? ("SIG\(record.signalNumber)", "Fatal signal.")
        let frames = record.frames.enumerated().map { index, address in
            JSONValue.object(frame(index: index, address: address, record: record))
        }
        return [
            "id": .string(id),
            "kind": .string("fatal"),
            "source": .string("sdk-signal"),
            "timestamp": .number(record.timestamp > 0 ? record.timestamp : Date().timeIntervalSince1970),
            "signal": .string(signal.name),
            "signal_number": .number(Double(record.signalNumber)),
            "signal_code": .number(Double(record.code)),
            "fault_address": .string("0x" + String(record.faultAddress, radix: 16)),
            "name": .string(signal.name),
            "reason": .string(signal.summary),
            "pid": .number(Double(record.processId)),
            "arch": .string(record.arch),
            "thread": .string(record.isMainThread ? "main" : "thread \(record.threadId)"),
            "executable": .string(executable),
            "previous_session": .bool(previousSession),
            "frames": .array(frames),
            "images": .array(record.images.map { JSONValue.object(imageObject($0)) })
        ]
    }

    private static func frame(index: Int, address: UInt64, record: CrashRecord) -> [String: JSONValue] {
        var row: [String: JSONValue] = [
            "index": .number(Double(index)),
            "address": .string("0x" + String(address, radix: 16))
        ]
        // Shared cache images report a very large end address, so the closest base wins.
        let owner = record.images.filter { $0.contains(address) }.max { $0.base < $1.base }
        guard let image = owner else {
            row["raw"] = .string("\(index)  ? 0x\(String(address, radix: 16))")
            return row
        }
        let offset = address - image.base
        row["image"] = .string(image.name)
        row["image_uuid"] = .string(image.uuid)
        row["image_base"] = .string("0x" + String(image.base, radix: 16))
        row["offset"] = .number(Double(offset))
        row["raw"] = .string("\(index)  \(image.name) 0x\(String(address, radix: 16)) + \(offset)")
        return row
    }

    private static func imageObject(_ image: CrashImage) -> [String: JSONValue] {
        [
            "name": .string(image.name),
            "path": .string(image.path),
            "uuid": .string(image.uuid),
            "base": .string("0x" + String(image.base, radix: 16))
        ]
    }
}
#endif
