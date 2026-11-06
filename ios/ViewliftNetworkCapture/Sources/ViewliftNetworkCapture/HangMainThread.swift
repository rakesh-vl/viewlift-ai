#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Darwin
import Foundation

/// Samples the main thread while it is blocked. Symbolication runs after resume.
enum HangMainThread {
    private static let lock = NSLock()
    private static var mainThread: pthread_t?

    static func remember() {
        lock.lock()
        mainThread = pthread_self()
        lock.unlock()
    }

    static func sampleSymbols() -> [String] {
        #if (os(iOS) || os(tvOS)) && arch(arm64)
        let addresses = suspendedAddresses()
        return addresses.enumerated().map { index, address in
            symbolLine(index: index, address: address)
        }
        #else
        return []
        #endif
    }

    private static func storedThread() -> pthread_t? {
        lock.lock()
        defer { lock.unlock() }
        return mainThread
    }

    #if (os(iOS) || os(tvOS)) && arch(arm64)
    private static func suspendedAddresses() -> [UInt64] {
        guard let pthread = storedThread(), pthread_equal(pthread, pthread_self()) == 0 else {
            return []
        }
        let machThread = pthread_mach_thread_np(pthread)
        guard thread_suspend(machThread) == KERN_SUCCESS else { return [] }
        defer { thread_resume(machThread) }
        return readAddresses(machThread)
    }

    private static func readAddresses(_ machThread: thread_act_t) -> [UInt64] {
        var state = arm_thread_state64_t()
        let words = MemoryLayout<arm_thread_state64_t>.size / MemoryLayout<UInt32>.size
        var count = mach_msg_type_number_t(words)
        let result = withUnsafeMutablePointer(to: &state) { pointer -> kern_return_t in
            pointer.withMemoryRebound(to: natural_t.self, capacity: Int(count)) {
                thread_get_state(machThread, ARM_THREAD_STATE64, $0, &count)
            }
        }
        guard result == KERN_SUCCESS else { return [] }
        var addresses = [strip(state.__pc)]
        var frame = strip(state.__fp)
        for _ in 0..<40 {
            guard let pair = readFrame(at: frame) else { break }
            let next = strip(pair.0)
            let link = strip(pair.1)
            if link > 0x1000 {
                addresses.append(link)
            }
            if next <= frame { break }
            frame = next
        }
        return addresses
    }

    private static func readFrame(at address: UInt64) -> (UInt64, UInt64)? {
        guard address > 0x1000, address % 8 == 0 else { return nil }
        var words = (UInt64(0), UInt64(0))
        var size: vm_size_t = 0
        let result = withUnsafeMutablePointer(to: &words) { pointer -> kern_return_t in
            vm_read_overwrite(
                mach_task_self_,
                vm_address_t(address),
                vm_size_t(MemoryLayout<(UInt64, UInt64)>.size),
                vm_address_t(bitPattern: pointer),
                &size
            )
        }
        guard result == KERN_SUCCESS, size >= 16 else { return nil }
        return words
    }

    private static func strip(_ value: UInt64) -> UInt64 {
        value & 0x00FF_FFFF_FFFF_FFFF
    }

    private static func symbolLine(index: Int, address: UInt64) -> String {
        var info = Dl_info()
        let pointer = UnsafeRawPointer(bitPattern: UInt(address))
        guard let pointer, dladdr(pointer, &info) != 0 else {
            return "\(index)   unknown   0x\(String(address, radix: 16))   0x\(String(address, radix: 16))"
        }
        let image = imageName(info.dli_fname)
        let symbol = demangle(info.dli_sname)
        return "\(index)   \(image)   0x\(String(address, radix: 16))   \(symbol)"
    }

    private static func imageName(_ pointer: UnsafePointer<CChar>?) -> String {
        guard let pointer else { return "unknown" }
        return URL(fileURLWithPath: String(cString: pointer)).lastPathComponent
    }

    private static func demangle(_ pointer: UnsafePointer<CChar>?) -> String {
        guard let pointer else { return "unknown" }
        let raw = String(cString: pointer)
        guard raw.hasPrefix("$s") || raw.hasPrefix("_T") else { return raw }
        guard let result = swift_demangle(pointer, raw.utf8.count, nil, nil, 0) else { return raw }
        defer { free(result) }
        return String(cString: result)
    }
    #endif
}

#if (os(iOS) || os(tvOS)) && arch(arm64)
@_silgen_name("swift_demangle")
private func swift_demangle(
    _ mangled: UnsafePointer<CChar>?,
    _ length: Int,
    _ output: UnsafeMutablePointer<CChar>?,
    _ outputLength: UnsafeMutablePointer<Int>?,
    _ flags: UInt32
) -> UnsafeMutablePointer<CChar>?
#endif
#endif
