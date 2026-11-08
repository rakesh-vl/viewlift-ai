import AVFoundation
import CoreMediaIO
import Foundation

/// Records the screen of a USB-connected iPhone or iPad, the same capture QuickTime uses.
///
/// Usage:
///   viewlift-screen-recorder list
///   viewlift-screen-recorder record <udid> <output.mov>
///
/// `record` runs until SIGINT or SIGTERM, then finishes the movie file and exits 0.
/// Output is one JSON object per line on stdout so the console can parse it.

enum RecorderError: Error, CustomStringConvertible {
    case usage
    case deviceNotFound(String)
    case cannotAddInput
    case cannotAddOutput
    case failed(String)

    var description: String {
        switch self {
        case .usage:
            return "usage: viewlift-screen-recorder list | record <udid> <output.mov>"
        case .deviceNotFound(let udid):
            return "No screen capture device for \(udid). Connect it over USB, unlock it, and trust this Mac."
        case .cannotAddInput:
            return "The capture session rejected the device input."
        case .cannotAddOutput:
            return "The capture session rejected the movie output."
        case .failed(let message):
            return message
        }
    }
}

func emit(_ object: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
          let line = String(data: data, encoding: .utf8) else { return }
    FileHandle.standardOutput.write(Data((line + "\n").utf8))
}

/// iOS devices only appear as capture devices after this CoreMediaIO property is set.
func allowScreenCaptureDevices() {
    var address = CMIOObjectPropertyAddress(
        mSelector: CMIOObjectPropertySelector(kCMIOHardwarePropertyAllowScreenCaptureDevices),
        mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
        mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain)
    )
    var allow: UInt32 = 1
    let size = UInt32(MemoryLayout<UInt32>.size)
    CMIOObjectSetPropertyData(CMIOObjectID(kCMIOObjectSystemObject), &address, 0, nil, size, &allow)
}

func screenDevices() -> [AVCaptureDevice] {
    let session = AVCaptureDevice.DiscoverySession(
        deviceTypes: [.external],
        mediaType: .muxed,
        position: .unspecified
    )
    return session.devices
}

func normalized(_ value: String) -> String {
    value.replacingOccurrences(of: "-", with: "").lowercased()
}

/// Devices appear a moment after the property is set, so poll briefly.
func findDevice(udid: String, timeout: TimeInterval = 8) -> AVCaptureDevice? {
    let wanted = normalized(udid)
    let deadline = Date().addingTimeInterval(timeout)
    repeat {
        if let match = screenDevices().first(where: { normalized($0.uniqueID) == wanted }) {
            return match
        }
        RunLoop.current.run(until: Date().addingTimeInterval(0.25))
    } while Date() < deadline
    return nil
}

final class Recorder: NSObject, AVCaptureFileOutputRecordingDelegate {
    private let session = AVCaptureSession()
    private let output = AVCaptureMovieFileOutput()
    private let finished = DispatchSemaphore(value: 0)
    private var failure: Error?

    func start(device: AVCaptureDevice, url: URL) throws {
        let input = try AVCaptureDeviceInput(device: device)
        session.beginConfiguration()
        guard session.canAddInput(input) else { throw RecorderError.cannotAddInput }
        session.addInput(input)
        guard session.canAddOutput(output) else { throw RecorderError.cannotAddOutput }
        session.addOutput(output)
        session.commitConfiguration()
        session.startRunning()
        try? FileManager.default.removeItem(at: url)
        output.startRecording(to: url, recordingDelegate: self)
    }

    func stop() throws {
        output.stopRecording()
        _ = finished.wait(timeout: .now() + 20)
        session.stopRunning()
        if let failure {
            throw RecorderError.failed(failure.localizedDescription)
        }
    }

    func fileOutput(
        _ output: AVCaptureFileOutput,
        didFinishRecordingTo outputFileURL: URL,
        from connections: [AVCaptureConnection],
        error: Error?
    ) {
        if let error {
            let nsError = error as NSError
            let completed = nsError.userInfo[AVErrorRecordingSuccessfullyFinishedKey] as? Bool ?? false
            if !completed { failure = error }
        }
        finished.signal()
    }
}

func waitForSignal() {
    let stopped = DispatchSemaphore(value: 0)
    var sources: [DispatchSourceSignal] = []
    for code in [SIGINT, SIGTERM] {
        signal(code, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: code, queue: .global())
        source.setEventHandler { stopped.signal() }
        source.resume()
        sources.append(source)
    }
    while stopped.wait(timeout: .now() + 0.2) == .timedOut {
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
    }
    sources.forEach { $0.cancel() }
}

func run(_ arguments: [String]) throws {
    allowScreenCaptureDevices()
    guard let command = arguments.first else { throw RecorderError.usage }
    switch command {
    case "list":
        RunLoop.current.run(until: Date().addingTimeInterval(1.5))
        let rows = screenDevices().map { ["udid": $0.uniqueID, "name": $0.localizedName] }
        emit(["devices": rows])
    case "record":
        guard arguments.count == 3 else { throw RecorderError.usage }
        guard let device = findDevice(udid: arguments[1]) else {
            throw RecorderError.deviceNotFound(arguments[1])
        }
        let url = URL(fileURLWithPath: arguments[2])
        let recorder = Recorder()
        try recorder.start(device: device, url: url)
        emit(["recording": true, "path": url.path, "device": device.localizedName])
        waitForSignal()
        try recorder.stop()
        emit(["recording": false, "path": url.path])
    default:
        throw RecorderError.usage
    }
}

do {
    try run(Array(CommandLine.arguments.dropFirst()))
} catch {
    emit(["error": String(describing: error)])
    exit(1)
}
