#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Darwin
import Foundation
import MachO

/// Builds a `CrashRecord` for an uncaught `NSException`. The handler runs on a normal thread, so
/// unlike the signal path it may use dladdr and allocate.
enum ExceptionCrashRecord {
    static let maxFrames = 128

    static func make(addresses: [UInt64]) -> CrashRecord {
        var record = CrashRecord()
        record.signalNumber = Int(SIGABRT)
        record.timestamp = Date().timeIntervalSince1970
        record.processId = Int(ProcessInfo.processInfo.processIdentifier)
        record.arch = architecture
        record.isMainThread = Thread.isMainThread
        record.frames = Array(addresses.prefix(maxFrames))
        var seen = Set<UInt64>()
        for address in record.frames {
            guard let image = image(containing: address), seen.insert(image.base).inserted else { continue }
            record.images.append(image)
        }
        return record
    }

    private static var architecture: String {
        #if arch(arm64)
        return "arm64"
        #elseif arch(x86_64)
        return "x86_64"
        #else
        return "unknown"
        #endif
    }

    private static func image(containing address: UInt64) -> CrashImage? {
        var info = Dl_info()
        guard address != 0, dladdr(UnsafeRawPointer(bitPattern: UInt(address)), &info) != 0,
              let base = info.dli_fbase else {
            return nil
        }
        let header = base.assumingMemoryBound(to: mach_header_64.self)
        guard header.pointee.magic == MH_MAGIC_64 else { return nil }
        let layout = segmentLayout(header)
        let start = UInt64(UInt(bitPattern: base))
        return CrashImage(
            base: start,
            end: max(layout.end, start + 1),
            uuid: layout.uuid,
            path: info.dli_fname.map { String(cString: $0) } ?? "?"
        )
    }

    /// Reads the UUID and the end address from the image's load commands.
    private static func segmentLayout(_ header: UnsafePointer<mach_header_64>) -> (uuid: String, end: UInt64) {
        let start = UInt64(UInt(bitPattern: header))
        var end = start
        var uuid = ""
        var textAddress: UInt64?
        var cursor = UnsafeRawPointer(header).advanced(by: MemoryLayout<mach_header_64>.size)
        for _ in 0..<header.pointee.ncmds {
            let command = cursor.assumingMemoryBound(to: load_command.self).pointee
            if command.cmd == UInt32(LC_UUID) {
                let item = cursor.assumingMemoryBound(to: uuid_command.self).pointee
                uuid = UUID(uuid: item.uuid).uuidString
            } else if command.cmd == UInt32(LC_SEGMENT_64) {
                let segment = cursor.assumingMemoryBound(to: segment_command_64.self).pointee
                let name = segmentName(segment)
                if name == "__TEXT" { textAddress = segment.vmaddr }
                if name != "__PAGEZERO", let text = textAddress {
                    // Segment addresses are relative to __TEXT, which sits at the header.
                    end = max(end, start + (segment.vmaddr - text) + segment.vmsize)
                }
            }
            cursor = cursor.advanced(by: Int(command.cmdsize))
        }
        return (uuid, end)
    }

    private static func segmentName(_ segment: segment_command_64) -> String {
        var name = segment.segname
        return withUnsafeBytes(of: &name) { raw in
            String(bytes: raw.prefix { $0 != 0 }, encoding: .utf8) ?? ""
        }
    }
}
#endif
