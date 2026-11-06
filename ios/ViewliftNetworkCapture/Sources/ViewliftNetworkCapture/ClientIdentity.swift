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

    /// Simulator processes inherit `SIMULATOR_UDID`, so every app on one simulator shares a key.
    static func resolve(
        environment: [String: String],
        vendorId: String?,
        deviceName: String,
        hardwareModel: String
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
            isSimulator: false
        )
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
        return resolve(
            environment: ProcessInfo.processInfo.environment,
            vendorId: vendorId,
            deviceName: name,
            hardwareModel: hardwareModel()
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
