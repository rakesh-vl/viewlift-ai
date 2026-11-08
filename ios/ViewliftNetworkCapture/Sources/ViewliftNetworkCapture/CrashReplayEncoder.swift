#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import AVFoundation
import CoreGraphics
import CoreText
import CoreVideo
import Foundation
import ImageIO

struct CrashReplayVideo: Equatable {
    let url: URL
    let frames: Int
    let durationSeconds: Double
    let width: Int
    let height: Int
}

enum CrashReplayEncoderError: Error, Equatable {
    case noFrames
    case writerFailed(String)
}

/// Turns a frozen replay folder into an H.264 MP4. Each frame keeps its real capture time, so pauses
/// look like pauses. Taps are drawn as circles and a caption shows the time left before the crash.
enum CrashReplayEncoder {
    static let fileName = "replay.mp4"
    static let averageBitRate = 600_000
    static let tapRadius: CGFloat = 16
    static let captionHeight: CGFloat = 24
    /// How long the last screen stays up after the crash moment.
    static let crashHoldSeconds = 0.5

    static func encode(folder: URL, crashTime: TimeInterval?) throws -> CrashReplayVideo {
        let slots = CrashReplayRing.load(from: folder)
        let images = slots.compactMap { slot in image(at: CrashReplayRing.frameURL(folder, slot: slot.slot)) }
        guard let firstImage = images.first, let start = slots.first?.time, slots.count == images.count else {
            throw CrashReplayEncoderError.noFrames
        }
        let width = max(2, firstImage.width / 2 * 2)
        let height = max(2, firstImage.height / 2 * 2)
        let output = folder.appendingPathComponent(fileName)
        try? FileManager.default.removeItem(at: output)
        let session = try WriterSession(output: output, width: width, height: height)
        let canvas = CGSize(width: width, height: height)
        var lastSeconds = -1.0
        for (slot, frame) in zip(slots, images) {
            let seconds = max(lastSeconds + 0.01, slot.time - start)
            let caption = Self.caption(slot: slot, crashTime: crashTime)
            try session.append(seconds: seconds) { context in
                draw(frame, touches: slot.touches, caption: caption, in: context, size: canvas)
            }
            lastSeconds = seconds
        }
        if let last = slots.last, let lastImage = images.last {
            // The final frame marks the crash moment, so the video ends where the app died.
            let end = min(lastSeconds + 2, max(lastSeconds + 0.25, (crashTime ?? last.time) - start))
            try session.append(seconds: end) { context in
                draw(lastImage, touches: [], caption: "Crash · \(last.screen)", in: context, size: canvas)
            }
            lastSeconds = end + crashHoldSeconds
        }
        try session.finish(at: lastSeconds)
        return CrashReplayVideo(
            url: output, frames: slots.count, durationSeconds: lastSeconds, width: width, height: height
        )
    }

    static func caption(slot: CrashReplaySlot, crashTime: TimeInterval?) -> String {
        let screen = slot.title.isEmpty ? slot.screen : "\(slot.screen) · \(slot.title)"
        guard let crashTime else { return screen }
        return String(format: "-%.1fs  %@", max(0, crashTime - slot.time), screen)
    }

    private static func image(at url: URL) -> CGImage? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
        return CGImageSourceCreateImageAtIndex(source, 0, nil)
    }

    private static func draw(
        _ image: CGImage,
        touches: [CrashReplayTouch],
        caption: String,
        in context: CGContext,
        size: CGSize
    ) {
        context.draw(image, in: CGRect(origin: .zero, size: size))
        for touch in touches {
            drawTouch(touch, in: context, size: size)
        }
        drawCaption(caption, in: context, size: size)
    }

    /// Touch points are top-left based; Core Graphics is bottom-left.
    private static func drawTouch(_ touch: CrashReplayTouch, in context: CGContext, size: CGSize) {
        if touch.phase == "press" {
            drawText("Remote: \(touch.label)", in: context, at: CGPoint(x: 8, y: size.height - 20), size: 13)
            return
        }
        let center = CGPoint(x: touch.pointX * size.width, y: (1 - touch.pointY) * size.height)
        let radius = touch.phase == "began" ? tapRadius : tapRadius * 0.6
        let rect = CGRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)
        context.setFillColor(CGColor(red: 1, green: 0.78, blue: 0.1, alpha: 0.45))
        context.fillEllipse(in: rect)
        context.setStrokeColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.95))
        context.setLineWidth(2)
        context.strokeEllipse(in: rect)
        if !touch.label.isEmpty {
            drawText(touch.label, in: context, at: CGPoint(x: center.x + radius + 4, y: center.y - 5), size: 12)
        }
    }

    private static func drawCaption(_ caption: String, in context: CGContext, size: CGSize) {
        context.setFillColor(CGColor(red: 0, green: 0, blue: 0, alpha: 0.65))
        context.fill(CGRect(x: 0, y: 0, width: size.width, height: captionHeight))
        drawText(caption, in: context, at: CGPoint(x: 8, y: 7), size: 12)
    }

    private static func drawText(_ text: String, in context: CGContext, at point: CGPoint, size: CGFloat) {
        let font = CTFontCreateWithName("Helvetica-Bold" as CFString, size, nil)
        let attributes: [NSAttributedString.Key: Any] = [
            NSAttributedString.Key(kCTFontAttributeName as String): font,
            NSAttributedString.Key(kCTForegroundColorAttributeName as String):
                CGColor(red: 1, green: 1, blue: 1, alpha: 1)
        ]
        let line = CTLineCreateWithAttributedString(NSAttributedString(string: text, attributes: attributes))
        context.textPosition = point
        CTLineDraw(line, context)
    }
}

/// One `AVAssetWriter` run. Appends are synchronous; the encoder runs off the main thread.
private final class WriterSession {
    private let writer: AVAssetWriter
    private let input: AVAssetWriterInput
    private let adaptor: AVAssetWriterInputPixelBufferAdaptor
    private let width: Int
    private let height: Int

    init(output: URL, width: Int, height: Int) throws {
        self.width = width
        self.height = height
        writer = try AVAssetWriter(outputURL: output, fileType: .mp4)
        input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: CrashReplayEncoder.averageBitRate,
                AVVideoProfileLevelKey: AVVideoProfileLevelH264MainAutoLevel
            ]
        ])
        input.expectsMediaDataInRealTime = false
        adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: width,
            kCVPixelBufferHeightKey as String: height,
            kCVPixelBufferCGImageCompatibilityKey as String: true,
            kCVPixelBufferCGBitmapContextCompatibilityKey as String: true
        ])
        guard writer.canAdd(input) else { throw CrashReplayEncoderError.writerFailed("cannot add video input") }
        writer.add(input)
        guard writer.startWriting() else { throw failure() }
        writer.startSession(atSourceTime: .zero)
    }

    func append(seconds: Double, draw: (CGContext) -> Void) throws {
        let buffer = try pixelBuffer()
        CVPixelBufferLockBaseAddress(buffer, [])
        if let context = CGContext(
            data: CVPixelBufferGetBaseAddress(buffer),
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: CVPixelBufferGetBytesPerRow(buffer),
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue
        ) {
            draw(context)
        }
        CVPixelBufferUnlockBaseAddress(buffer, [])
        var waited = 0
        while !input.isReadyForMoreMediaData, waited < 400 {
            Thread.sleep(forTimeInterval: 0.005)
            waited += 1
        }
        let time = CMTime(seconds: seconds, preferredTimescale: 600)
        guard adaptor.append(buffer, withPresentationTime: time) else { throw failure() }
    }

    func finish(at seconds: Double) throws {
        input.markAsFinished()
        writer.endSession(atSourceTime: CMTime(seconds: seconds, preferredTimescale: 600))
        let done = DispatchSemaphore(value: 0)
        writer.finishWriting { done.signal() }
        done.wait()
        guard writer.status == .completed else { throw failure() }
    }

    private func pixelBuffer() throws -> CVPixelBuffer {
        var buffer: CVPixelBuffer?
        if let pool = adaptor.pixelBufferPool {
            CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer)
        }
        if buffer == nil {
            CVPixelBufferCreate(nil, width, height, kCVPixelFormatType_32BGRA, nil, &buffer)
        }
        guard let buffer else { throw CrashReplayEncoderError.writerFailed("no pixel buffer") }
        return buffer
    }

    private func failure() -> CrashReplayEncoderError {
        .writerFailed(writer.error.map { String(describing: $0) } ?? "status \(writer.status.rawValue)")
    }
}
#endif
