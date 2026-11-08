import CryptoKit
import Foundation

#if canImport(UIKit)
import UIKit
#endif

/// Supplies the per-install client id shared by capture uploads and the debug bridge (DIP).
public protocol ClientIdentityProviding: Sendable {
    var clientId: String { get }
}

/// Per-install UUID persisted in UserDefaults. Separate apps on one device get separate ids.
public final class UserDefaultsClientIdentity: ClientIdentityProviding, @unchecked Sendable {
    public static let shared = UserDefaultsClientIdentity()
    static let storageKey = "com.viewlift.networkcapture.client-id"

    public let clientId: String

    init(defaults: UserDefaults = .standard) {
        if let existing = defaults.string(forKey: Self.storageKey), !existing.isEmpty {
            clientId = existing
        } else {
            let generated = UUID().uuidString
            defaults.set(generated, forKey: Self.storageKey)
            clientId = generated
        }
    }
}

/// Groups app instances that run on the same simulator or physical device.
public struct DeviceIdentity: Sendable, Equatable {
    public static let sdkVersion = "1.1.0"

    public let deviceKey: String
    public let deviceName: String
    public let deviceModel: String
    public let isSimulator: Bool
    /// Same on every app of one physical device, whatever its vendor, until the device reboots.
    /// Lets the console group apps whose vendor identifiers differ. Empty on simulators.
    public var deviceHint = ""

    /// Simulator processes inherit `SIMULATOR_UDID`, so every app on one simulator shares a key.
    static func resolve(
        environment: [String: String],
        vendorId: String?,
        deviceName: String,
        hardwareModel: String,
        deviceHint: String = ""
    ) -> DeviceIdentity {
        if let udid = environment["SIMULATOR_UDID"], !udid.isEmpty {
            return DeviceIdentity(
                deviceKey: udid,
                deviceName: environment["SIMULATOR_DEVICE_NAME"] ?? deviceName,
                deviceModel: environment["SIMULATOR_MODEL_IDENTIFIER"] ?? hardwareModel,
                isSimulator: true
            )
        }
        let fallback = "\(hardwareModel)|\(deviceName)"
        return DeviceIdentity(
            deviceKey: vendorId.map { "vendor:\($0)" } ?? "hint:\(fallback)",
            deviceName: deviceName,
            deviceModel: hardwareModel,
            isSimulator: false,
            deviceHint: deviceHint
        )
    }

    /// Hash of the hardware model, OS version, and boot time. The UI runner computes the same value.
    static func deviceHint(hardwareModel: String, osVersion: String, bootTime: Int) -> String {
        guard bootTime > 0 else { return "" }
        let digest = SHA256.hash(data: Data("\(hardwareModel)|\(osVersion)|\(bootTime)".utf8))
        return digest.prefix(8).map { String(format: "%02x", $0) }.joined()
    }

    static func osVersion() -> String {
        let version = ProcessInfo.processInfo.operatingSystemVersion
        return "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)"
    }

    static func bootTime() -> Int {
        var value = timeval()
        var size = MemoryLayout<timeval>.size
        guard sysctlbyname("kern.boottime", &value, &size, nil, 0) == 0 else { return 0 }
        return Int(value.tv_sec)
    }

    @MainActor
    public static func current() -> DeviceIdentity {
        #if canImport(UIKit) && !os(watchOS)
        let vendorId = UIDevice.current.identifierForVendor?.uuidString
        let name = UIDevice.current.name
        #else
        let vendorId: String? = nil
        let name = ProcessInfo.processInfo.hostName
        #endif
        let model = hardwareModel()
        return resolve(
            environment: ProcessInfo.processInfo.environment,
            vendorId: vendorId,
            deviceName: name,
            hardwareModel: model,
            deviceHint: deviceHint(hardwareModel: model, osVersion: osVersion(), bootTime: bootTime())
        )
    }

    private static func hardwareModel() -> String {
        var info = utsname()
        uname(&info)
        return withUnsafeBytes(of: &info.machine) { buffer in
            String(bytes: buffer.prefix { $0 != 0 }, encoding: .utf8) ?? ""
        }
    }
}
