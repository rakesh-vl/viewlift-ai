import Foundation

/// Launch settings from the console. `xcodebuild test-without-building` passes every
/// `TEST_RUNNER_<NAME>` variable to this process as `<NAME>`.
struct RunnerConfig {
    let receiverURL: URL
    let fingerprint: String
    let debugKey: String
    let bundleID: String
    let clientID: String
    let deviceKey: String
    let pollInterval: TimeInterval

    enum Key {
        static let url = "VIEWLIFT_RUNNER_URL"
        static let fingerprint = "VIEWLIFT_RUNNER_FINGERPRINT"
        static let debugKey = "VIEWLIFT_RUNNER_DEBUG_KEY"
        static let bundleID = "VIEWLIFT_RUNNER_BUNDLE_ID"
        static let clientID = "VIEWLIFT_RUNNER_CLIENT_ID"
        static let deviceKey = "VIEWLIFT_RUNNER_DEVICE_KEY"
        static let pollInterval = "VIEWLIFT_RUNNER_POLL_SECONDS"
    }

    static func load(from env: [String: String] = ProcessInfo.processInfo.environment) -> RunnerConfig? {
        guard let rawURL = value(env, Key.url),
              let url = URL(string: rawURL),
              url.scheme == "https",
              let fingerprint = value(env, Key.fingerprint),
              let debugKey = value(env, Key.debugKey),
              let bundleID = value(env, Key.bundleID),
              let clientID = value(env, Key.clientID) else {
            return nil
        }
        let poll = value(env, Key.pollInterval).flatMap(Double.init) ?? 0.25
        return RunnerConfig(
            receiverURL: url,
            fingerprint: fingerprint,
            debugKey: debugKey,
            bundleID: bundleID,
            clientID: clientID,
            deviceKey: value(env, Key.deviceKey) ?? clientID,
            pollInterval: min(max(poll, 0.05), 2.0)
        )
    }

    private static func value(_ env: [String: String], _ key: String) -> String? {
        guard let raw = env[key]?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else {
            return nil
        }
        return raw
    }
}
