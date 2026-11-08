#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Foundation

#if canImport(UIKit)
import UIKit

/// On-device layout overlays and 5× slower animations. Mirrors Flutter's visual debugging toggles.
@MainActor
final class UILayoutDebugService: NSObject {
    static let shared = UILayoutDebugService()

    private struct Flags {
        var slow = false
        var guidelines = false
        var baselines = false
        var oversized = false
        var repaint = false

        var paints: Bool { guidelines || baselines || oversized || repaint }
        var active: Bool { slow || paints }
    }

    private var flags = Flags()
    private var link: CADisplayLink?
    private var canvas: UILayoutDebugCanvas?
    private var repaints: [String: CFTimeInterval] = [:]
    private let repaintLock = NSLock()
    private var swizzled = false

    func apply(_ payload: [String: JSONValue]) -> [String: JSONValue] {
        flags.slow = bool(payload["slow"])
        flags.guidelines = bool(payload["guidelines"])
        flags.baselines = bool(payload["baselines"])
        flags.oversized = bool(payload["oversized"])
        flags.repaint = bool(payload["repaint"])
        if flags.repaint { installRepaintHook() }
        applySpeed(flags.slow ? 0.2 : 1)
        syncLink()
        return snapshot()
    }

    func noteRepaint(_ view: UIView) {
        guard flags.repaint, !(view is UILayoutDebugCanvas) else { return }
        let identifier = String(ObjectIdentifier(view).hashValue)
        repaintLock.lock()
        repaints[identifier] = CACurrentMediaTime()
        if repaints.count > 2_000 {
            let cutoff = CACurrentMediaTime() - 2
            repaints = repaints.filter { $0.value >= cutoff }
        }
        repaintLock.unlock()
    }

    static func isOverlay(_ view: UIView) -> Bool {
        view is UILayoutDebugCanvas
    }

    @objc private func tick() {
        MainActor.assumeIsolated { self.refresh() }
    }

    private func refresh() {
        applySpeed(flags.slow ? 0.2 : 1)
        guard flags.paints, let window = keyWindow() else {
            canvas?.removeFromSuperview()
            canvas = nil
            return
        }
        let overlay = overlay(on: window)
        window.bringSubviewToFront(overlay)
        overlay.frame = window.bounds
        overlay.setNeedsDisplay()
    }

    private func syncLink() {
        guard flags.active else {
            link?.invalidate()
            link = nil
            canvas?.removeFromSuperview()
            canvas = nil
            return
        }
        guard link == nil else {
            tick()
            return
        }
        let display = CADisplayLink(target: self, selector: #selector(tick))
        display.preferredFramesPerSecond = 8
        display.add(to: .main, forMode: .common)
        link = display
        tick()
    }

    private func snapshot() -> [String: JSONValue] {
        [
            "slow": .bool(flags.slow),
            "guidelines": .bool(flags.guidelines),
            "baselines": .bool(flags.baselines),
            "oversized": .bool(flags.oversized),
            "repaint": .bool(flags.repaint)
        ]
    }

    private func applySpeed(_ speed: Float) {
        for window in windows() where window.layer.speed != speed {
            let now = CACurrentMediaTime()
            let local = window.layer.convertTime(now, from: nil)
            window.layer.speed = speed
            window.layer.timeOffset = local
            window.layer.beginTime = now
        }
    }

    private func overlay(on window: UIWindow) -> UILayoutDebugCanvas {
        if let canvas, canvas.superview === window { return canvas }
        canvas?.removeFromSuperview()
        let created = UILayoutDebugCanvas(frame: window.bounds)
        window.addSubview(created)
        canvas = created
        return created
    }

    private func installRepaintHook() {
        guard !swizzled else { return }
        swizzled = true
        swap(UIView.self, NSSelectorFromString("setNeedsDisplay"), #selector(UIView.viewliftSetNeedsDisplay))
        swap(CALayer.self, NSSelectorFromString("setNeedsDisplay"), #selector(CALayer.viewliftSetNeedsDisplay))
    }

    private func swap(_ type: AnyClass, _ original: Selector, _ replacement: Selector) {
        guard let source = class_getInstanceMethod(type, original),
              let target = class_getInstanceMethod(type, replacement) else {
            return
        }
        method_exchangeImplementations(source, target)
    }

    fileprivate func render(canvas: UILayoutDebugCanvas) {
        guard let context = UIGraphicsGetCurrentContext(), let window = canvas.superview as? UIWindow else { return }
        var remaining = 800
        visit(window, canvas: canvas, context: context, remaining: &remaining)
    }

    private func visit(
        _ view: UIView,
        canvas: UILayoutDebugCanvas,
        context: CGContext,
        remaining: inout Int
    ) {
        remaining -= 1
        let hidden = view.isHidden || view.alpha <= 0.01
        if view !== canvas, !hidden {
            paints(view, in: canvas, context: context)
        }
        guard remaining > 0 else { return }
        for child in view.subviews where !(child is UILayoutDebugCanvas) {
            visit(child, canvas: canvas, context: context, remaining: &remaining)
        }
    }

    fileprivate func paints(_ view: UIView, in canvas: UILayoutDebugCanvas, context: CGContext) {
        let rect = view.convert(view.bounds, to: canvas)
        guard rect.width > 0.5, rect.height > 0.5, canvas.bounds.intersects(rect) else { return }
        if flags.guidelines { paintGuidelines(view, rect: rect, context: context) }
        if flags.baselines { paintBaseline(view, rect: rect, context: context) }
        if flags.oversized { paintOversized(view, rect: rect, context: context) }
        if flags.repaint { paintRepaint(view, rect: rect, context: context) }
    }

    private func paintGuidelines(_ view: UIView, rect: CGRect, context: CGContext) {
        context.saveGState()
        context.setStrokeColor(UIColor.systemBlue.withAlphaComponent(0.9).cgColor)
        context.setLineWidth(1)
        context.stroke(rect)
        if let padding = UILayoutExplorer.notablePadding(of: view) {
            let inner = rect.inset(by: UIEdgeInsets(
                top: padding[0], left: padding[1], bottom: padding[2], right: padding[3]
            ))
            if inner.width > 0, inner.height > 0, inner != rect {
                context.setFillColor(UIColor.systemBlue.withAlphaComponent(0.16).cgColor)
                context.addRect(rect)
                context.addRect(inner)
                context.fillPath(using: .evenOdd)
            }
        }
        if view.clipsToBounds {
            context.setStrokeColor(UIColor.systemPink.cgColor)
            context.setLineDash(phase: 0, lengths: [4, 3])
            context.stroke(rect)
        }
        context.restoreGState()
    }

    private func paintBaseline(_ view: UIView, rect: CGRect, context: CGContext) {
        guard let label = view as? UILabel else { return }
        let local = label.textRect(forBounds: label.bounds, limitedToNumberOfLines: label.numberOfLines)
        let baseline = local.minY + label.font.ascender
        context.saveGState()
        context.setStrokeColor(UIColor.systemGreen.cgColor)
        context.setLineWidth(1)
        context.move(to: CGPoint(x: rect.minX, y: rect.minY + baseline))
        context.addLine(to: CGPoint(x: rect.maxX, y: rect.minY + baseline))
        context.strokePath()
        context.restoreGState()
    }

    private func paintOversized(_ view: UIView, rect: CGRect, context: CGContext) {
        guard let image = UILayoutExplorer.imageLoad(of: view), image.extraKilobytes >= 128 else { return }
        context.saveGState()
        context.setStrokeColor(UIColor.systemPink.cgColor)
        context.setLineWidth(3)
        context.stroke(rect)
        let text = "\(image.pixelWidth)×\(image.pixelHeight) draws \(image.displayWidth)×\(image.displayHeight)"
        let attributes: [NSAttributedString.Key: Any] = [
            .font: UIFont.monospacedSystemFont(ofSize: 10, weight: .semibold),
            .foregroundColor: UIColor.systemPink
        ]
        (text as NSString).draw(at: CGPoint(x: rect.minX + 4, y: rect.minY + 4), withAttributes: attributes)
        context.restoreGState()
    }

    private func paintRepaint(_ view: UIView, rect: CGRect, context: CGContext) {
        let identifier = String(ObjectIdentifier(view).hashValue)
        repaintLock.lock()
        let stamped = repaints[identifier]
        repaintLock.unlock()
        guard let stamped, CACurrentMediaTime() - stamped < 0.6 else { return }
        let hue = CGFloat(Int(CACurrentMediaTime() * 4) % 12) / 12
        context.saveGState()
        context.setStrokeColor(UIColor(hue: hue, saturation: 0.85, brightness: 0.95, alpha: 1).cgColor)
        context.setLineWidth(2)
        context.stroke(rect.insetBy(dx: 1, dy: 1))
        context.restoreGState()
    }

    private func keyWindow() -> UIWindow? {
        windows().first(where: \.isKeyWindow) ?? windows().first
    }

    private func windows() -> [UIWindow] {
        AutomationScreen.allWindows()
    }

    private func bool(_ value: JSONValue?) -> Bool {
        if case let .bool(item) = value { return item }
        return false
    }
}

private final class UILayoutDebugCanvas: UIView {
    override init(frame: CGRect) {
        super.init(frame: frame)
        isUserInteractionEnabled = false
        isAccessibilityElement = false
        accessibilityElementsHidden = true
        backgroundColor = .clear
        isOpaque = false
        autoresizingMask = [.flexibleWidth, .flexibleHeight]
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView? {
        nil
    }

    override func draw(_ rect: CGRect) {
        MainActor.assumeIsolated {
            UILayoutDebugService.shared.render(canvas: self)
        }
    }
}

private extension UIView {
    @objc func viewliftSetNeedsDisplay() {
        viewliftSetNeedsDisplay()
        guard Thread.isMainThread else { return }
        MainActor.assumeIsolated {
            UILayoutDebugService.shared.noteRepaint(self)
        }
    }
}

private extension CALayer {
    @objc func viewliftSetNeedsDisplay() {
        viewliftSetNeedsDisplay()
        guard Thread.isMainThread, let view = delegate as? UIView else { return }
        MainActor.assumeIsolated {
            UILayoutDebugService.shared.noteRepaint(view)
        }
    }
}
#endif
#endif
