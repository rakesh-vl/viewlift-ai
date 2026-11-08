import CryptoKit
import Foundation

/// Mirrors `DeviceIdentity.deviceHint` in ViewliftNetworkCapture so the console can pair
/// the runner with SDK apps on the same physical device. Keep the two in sync.
enum DeviceHint {
    static func current(hardwareModel: String) -> String {
        let version = ProcessInfo.processInfo.operatingSystemVersion
        let osVersion = "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)"
        var boot = timeval()
        var size = MemoryLayout<timeval>.size
        guard sysctlbyname("kern.boottime", &boot, &size, nil, 0) == 0, boot.tv_sec > 0 else { return "" }
        let digest = SHA256.hash(data: Data("\(hardwareModel)|\(osVersion)|\(boot.tv_sec)".utf8))
        return digest.prefix(8).map { String(format: "%02x", $0) }.joined()
    }
}
