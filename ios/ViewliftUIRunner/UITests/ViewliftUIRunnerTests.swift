import XCTest

/// Long-running test that serves console UI commands until the host stops `xcodebuild`.
/// It registers like an SDK app, so MCP and the dashboard drive it with the same commands.
final class ViewliftUIRunnerTests: XCTestCase {
    private static let reconnectDelay: TimeInterval = 2

    override func setUp() {
        super.setUp()
        continueAfterFailure = true
        executionTimeAllowance = 24 * 60 * 60
    }

    func testServeConsoleCommands() throws {
        let config = try XCTUnwrap(
            RunnerConfig.load(),
            "Start the runner from the Viewlift AI console; it passes the receiver settings."
        )
        let commands = RunnerCommands(bundleID: config.bundleID)
        if commands.app.state != .runningForeground {
            commands.app.activate()
        }
        let transport = RunnerTransport(config: config)
        while true {
            do {
                try transport.connect(registration: registration(config))
                try serve(transport: transport, commands: commands, config: config)
            } catch {
                NSLog("Viewlift UI runner: %@", String(describing: error))
                Thread.sleep(forTimeInterval: Self.reconnectDelay)
            }
        }
    }

    private func serve(transport: RunnerTransport, commands: RunnerCommands, config: RunnerConfig) throws {
        while true {
            for command in try transport.pollCommands() {
                try transport.send(event: result(of: command, commands: commands))
            }
            Thread.sleep(forTimeInterval: config.pollInterval)
        }
    }

    private func result(of command: [String: Any], commands: RunnerCommands) -> [String: Any] {
        let commandID = command["id"] as? String ?? ""
        let name = command["name"] as? String ?? ""
        let payload = command["payload"] as? [String: Any] ?? [:]
        let module = name.split(separator: ".").first.map(String.init) ?? "ui"
        var body: [String: Any] = ["command_id": commandID, "name": name]
        do {
            body["ok"] = true
            body["result"] = try commands.execute(name: name, payload: payload)
        } catch {
            body["ok"] = false
            body["error"] = String(describing: error)
        }
        return ["module": module, "kind": "command-result", "payload": body]
    }

    private func registration(_ config: RunnerConfig) -> [String: Any] {
        var info = utsname()
        uname(&info)
        let model = withUnsafeBytes(of: &info.machine) { buffer in
            String(bytes: buffer.prefix { $0 != 0 }, encoding: .utf8)
        } ?? ""
        let environment = ProcessInfo.processInfo.environment
        let isSimulator = environment["SIMULATOR_UDID"] != nil
        return [
            "client_id": config.clientID,
            "app_name": "UI runner · \(config.bundleID)",
            "bundle_id": config.bundleID,
            "platform": Self.platform,
            "os_version": ProcessInfo.processInfo.operatingSystemVersionString,
            "capabilities": ["ui": "available", "runner": "available"],
            "device_key": config.deviceKey,
            "device_name": environment["SIMULATOR_DEVICE_NAME"] ?? "",
            "device_model": environment["SIMULATOR_MODEL_IDENTIFIER"] ?? model,
            "is_simulator": isSimulator,
            "device_hint": isSimulator ? "" : DeviceHint.current(hardwareModel: model),
            "sdk_version": "runner-1"
        ]
    }

    private static var platform: String {
        #if os(tvOS)
        return "tvos"
        #else
        return "ios"
        #endif
    }
}
