#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import AVFoundation
import Foundation

/// KVO and notifications for one `AVPlayer`. Does not read asset URLs or license bytes.
final class PlaybackPlayerBinding: NSObject {
    private weak var player: AVPlayer?
    private weak var service: PlaybackDebugService?
    private var observations: [NSKeyValueObservation] = []
    private var itemObservations: [NSKeyValueObservation] = []
    private var tokens: [NSObjectProtocol] = []
    private var itemTokens: [NSObjectProtocol] = []
    private var stalled = false
    private var bufferEmpty = false
    private var lastStatus = ""
    private var lastReason = ""
    private var lastExternal = false
    private var lastBitrate = -1.0
    private var lastDropped = -1
    private var lastSize = CGSize.zero
    private var lastLag = -1.0
    private var lastLagAt = Date.distantPast
    private var lastErrorKey = ""
    private var sawExternal = false

    var current: AVPlayer? { player }

    func bind(_ player: AVPlayer, service: PlaybackDebugService) {
        stop()
        self.player = player
        self.service = service
        resetCounters()
        observePlayer(player)
        observeRoute()
        bindItem(player.currentItem)
    }

    func stop() {
        observations.forEach { $0.invalidate() }
        itemObservations.forEach { $0.invalidate() }
        observations.removeAll()
        itemObservations.removeAll()
        let center = NotificationCenter.default
        tokens.forEach { center.removeObserver($0) }
        itemTokens.forEach { center.removeObserver($0) }
        tokens.removeAll()
        itemTokens.removeAll()
        player = nil
        service = nil
    }

    private func resetCounters() {
        stalled = false
        bufferEmpty = false
        lastStatus = ""
        lastReason = ""
        lastExternal = false
        lastBitrate = -1
        lastDropped = -1
        lastSize = .zero
        lastLag = -1
        lastLagAt = .distantPast
        lastErrorKey = ""
        sawExternal = false
    }

    private func observePlayer(_ player: AVPlayer) {
        observations.append(player.observe(\.timeControlStatus, options: [.initial, .new]) { [weak self] item, _ in
            DispatchQueue.main.async { self?.statusChanged(item) }
        })
        observations.append(player.observe(\.currentItem, options: [.new]) { [weak self] item, _ in
            DispatchQueue.main.async { self?.bindItem(item.currentItem) }
        })
        #if os(iOS) || os(tvOS)
        observations.append(player.observe(\.isExternalPlaybackActive, options: [.new]) { [weak self] item, _ in
            let active = item.isExternalPlaybackActive
            DispatchQueue.main.async { self?.externalChanged(active) }
        })
        #endif
    }

    private func observeRoute() {
        #if os(iOS) || os(tvOS)
        let token = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: AVAudioSession.sharedInstance(),
            queue: .main
        ) { [weak self] note in
            self?.routeChanged(note)
        }
        tokens.append(token)
        #endif
    }

    private func bindItem(_ item: AVPlayerItem?) {
        itemObservations.forEach { $0.invalidate() }
        itemObservations.removeAll()
        let center = NotificationCenter.default
        itemTokens.forEach { center.removeObserver($0) }
        itemTokens.removeAll()
        guard let item else { return }
        observeItem(item)
        observeItemNotes(item)
    }

    private func observeItem(_ item: AVPlayerItem) {
        itemObservations.append(item.observe(\.status, options: [.new]) { [weak self] current, _ in
            let failed = current.status == .failed
            let error = current.error
            DispatchQueue.main.async {
                guard failed else { return }
                self?.noteFailure(error)
            }
        })
        itemObservations.append(item.observe(\.isPlaybackBufferEmpty, options: [.new]) { [weak self] current, _ in
            let empty = current.isPlaybackBufferEmpty
            DispatchQueue.main.async { self?.bufferChanged(empty: empty) }
        })
        itemObservations.append(item.observe(\.isPlaybackLikelyToKeepUp, options: [.new]) { [weak self] current, _ in
            let likely = current.isPlaybackLikelyToKeepUp
            DispatchQueue.main.async { self?.bufferLikely(likely) }
        })
        itemObservations.append(item.observe(\.loadedTimeRanges, options: [.new]) { [weak self] current, _ in
            DispatchQueue.main.async { self?.noteLiveEdge(current) }
        })
        itemObservations.append(item.observe(\.presentationSize, options: [.new]) { [weak self] current, _ in
            let size = current.presentationSize
            DispatchQueue.main.async { self?.noteVariant(size) }
        })
    }

    private func observeItemNotes(_ item: AVPlayerItem) {
        let center = NotificationCenter.default
        itemTokens.append(center.addObserver(
            forName: AVPlayerItem.playbackStalledNotification,
            object: item,
            queue: .main
        ) { [weak self] _ in
            self?.stallBegan()
        })
        itemTokens.append(center.addObserver(
            forName: AVPlayerItem.timeJumpedNotification,
            object: item,
            queue: .main
        ) { [weak self] _ in
            self?.noteSeek()
        })
        itemTokens.append(center.addObserver(
            forName: AVPlayerItem.failedToPlayToEndTimeNotification,
            object: item,
            queue: .main
        ) { [weak self] note in
            let error = note.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? Error
            self?.noteFailure(error)
        })
        itemTokens.append(center.addObserver(
            forName: AVPlayerItem.newAccessLogEntryNotification,
            object: item,
            queue: .main
        ) { [weak self] _ in
            self?.noteAccessLog(item)
        })
        itemTokens.append(center.addObserver(
            forName: AVPlayerItem.newErrorLogEntryNotification,
            object: item,
            queue: .main
        ) { [weak self] _ in
            self?.noteErrorLog(item)
        })
    }

    private func statusChanged(_ player: AVPlayer) {
        let name = Self.statusName(player.timeControlStatus)
        let reason = Self.reasonName(player.reasonForWaitingToPlay)
        service?.update { digest in
            digest.status = name
            digest.waitingReason = reason
        }
        if name == "playing", stalled {
            stalled = false
            service?.update { $0.stalled = false }
            service?.record(kind: "stall_end", fields: [:])
        }
        guard name != lastStatus || reason != lastReason else { return }
        lastStatus = name
        lastReason = reason
        service?.record(kind: "status", fields: [
            "status": .string(name),
            "waiting_reason": .string(reason)
        ])
    }

    private func stallBegan() {
        guard !stalled else { return }
        stalled = true
        service?.update { $0.stalled = true }
        service?.record(kind: "stall_start", fields: [:])
    }

    private func bufferChanged(empty: Bool) {
        guard empty, !bufferEmpty else { return }
        bufferEmpty = true
        service?.record(kind: "buffer_empty", fields: [:])
    }

    private func bufferLikely(_ likely: Bool) {
        guard likely, bufferEmpty else { return }
        bufferEmpty = false
        service?.record(kind: "buffer_ready", fields: [:])
    }

    private func noteSeek() {
        guard let player else { return }
        let seconds = CMTimeGetSeconds(player.currentTime())
        let playhead = seconds.isFinite ? seconds : 0
        service?.record(kind: "seek", fields: ["playhead_sec": .number(playhead)])
    }

    private func noteLiveEdge(_ item: AVPlayerItem) {
        guard item.duration.isIndefinite, let player else { return }
        guard let value = item.seekableTimeRanges.last else { return }
        let edge = CMTimeGetSeconds(CMTimeRangeGetEnd(value.timeRangeValue))
        let current = CMTimeGetSeconds(player.currentTime())
        guard edge.isFinite, current.isFinite else { return }
        let lag = max(0, edge - current)
        let due = Date().timeIntervalSince(lastLagAt) >= 1
        guard lastLag < 0 || (abs(lag - lastLag) >= 0.5 && due) else { return }
        lastLag = lag
        lastLagAt = Date()
        service?.update { $0.lag = lag }
        service?.record(kind: "live_edge", fields: [
            "lag_sec": .number(lag),
            "edge_sec": .number(edge)
        ])
    }

    private func noteVariant(_ size: CGSize) {
        guard size.width > 0, size.height > 0, size != lastSize else { return }
        lastSize = size
        service?.update { digest in
            digest.width = Double(size.width)
            digest.height = Double(size.height)
        }
        service?.record(kind: "variant", fields: [
            "width": .number(Double(size.width)),
            "height": .number(Double(size.height))
        ])
    }

}

extension PlaybackPlayerBinding {
    /// Bitrate and dropped frames only. `uri` is intentionally not read.
    fileprivate func noteAccessLog(_ item: AVPlayerItem) {
        guard let event = item.accessLog()?.events.last else { return }
        let observed = event.observedBitrate
        let indicated = event.indicatedBitrate
        if observed > 0, lastBitrate < 0 || abs(observed - lastBitrate) / max(observed, 1) > 0.08 {
            lastBitrate = observed
            service?.update { digest in
                digest.observed = observed
                digest.indicated = indicated
            }
            service?.record(kind: "bitrate", fields: [
                "observed_bps": .number(observed),
                "indicated_bps": .number(indicated)
            ])
        }
        let dropped = event.numberOfDroppedVideoFrames
        guard dropped > lastDropped, lastDropped >= 0 else {
            lastDropped = max(lastDropped, dropped)
            return
        }
        let delta = dropped - lastDropped
        lastDropped = dropped
        service?.update { $0.dropped = dropped }
        service?.record(kind: "dropped_frames", fields: [
            "delta": .number(Double(delta)),
            "total": .number(Double(dropped))
        ])
    }

    /// Domain and status code only. The error-log URI is not read.
    private func noteErrorLog(_ item: AVPlayerItem) {
        guard let event = item.errorLog()?.events.last else { return }
        let key = "\(event.errorDomain)#\(event.errorStatusCode)"
        guard key != lastErrorKey else { return }
        lastErrorKey = key
        noteFailure(domain: event.errorDomain, code: event.errorStatusCode, kind: "item_error_log")
    }

    private func noteFailure(_ error: Error?) {
        let nsError = error as NSError?
        noteFailure(domain: nsError?.domain ?? "", code: nsError?.code ?? 0, kind: "item_failed")
    }

    private func noteFailure(domain: String, code: Int, kind: String) {
        service?.update { digest in
            digest.errorDomain = domain
            digest.errorCode = code
        }
        service?.record(kind: kind, fields: [
            "error_domain": .string(domain),
            "error_code": .number(Double(code))
        ])
    }

    #if os(iOS) || os(tvOS)
    private func externalChanged(_ active: Bool) {
        service?.update { $0.external = active }
        guard !sawExternal || active != lastExternal else { return }
        sawExternal = true
        lastExternal = active
        service?.record(kind: "external_playback", fields: ["active": .bool(active)])
    }
    #endif

    #if os(iOS) || os(tvOS)
    private func routeChanged(_ note: Notification) {
        let reasonValue = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
        let outputs = AVAudioSession.sharedInstance().currentRoute.outputs.map { $0.portType.rawValue }
        service?.record(kind: "route", fields: [
            "reason": .string(Self.routeReason(reasonValue)),
            "outputs": .array(outputs.map(JSONValue.string))
        ])
    }
    #endif

    private static func statusName(_ status: AVPlayer.TimeControlStatus) -> String {
        switch status {
        case .playing:
            return "playing"
        case .paused:
            return "paused"
        case .waitingToPlayAtSpecifiedRate:
            return "waiting"
        @unknown default:
            return "unknown"
        }
    }

    private static func reasonName(_ reason: AVPlayer.WaitingReason?) -> String {
        switch reason {
        case .evaluatingBufferingRate:
            return "evaluating_buffer"
        case .noItemToPlay:
            return "no_item"
        case .toMinimizeStalls:
            return "minimize_stalls"
        default:
            return ""
        }
    }

    #if os(iOS) || os(tvOS)
    private static func routeReason(_ raw: UInt?) -> String {
        guard let raw, let reason = AVAudioSession.RouteChangeReason(rawValue: raw) else {
            return ""
        }
        switch reason {
        case .newDeviceAvailable:
            return "new_device"
        case .oldDeviceUnavailable:
            return "device_gone"
        case .categoryChange:
            return "category"
        case .override:
            return "override"
        case .routeConfigurationChange:
            return "configuration"
        default:
            return "other"
        }
    }
    #endif
}
#endif
