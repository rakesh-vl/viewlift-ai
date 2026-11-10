import Foundation

/// Who is signed in on this install. The console filters traffic by these fields.
///
/// `emailMask` is a display label only (`a***@example.com`). A raw address is dropped.
/// `userId` is empty for guests. `anonymousId` is the guest id when the app has one.
public struct CaptureSessionIdentity: Codable, Sendable, Equatable {
    public var authState: String
    public var userId: String
    public var anonymousId: String
    public var emailMask: String
    public var profileId: String
    public var loginProvider: String

    public init(
        authState: String,
        userId: String = "",
        anonymousId: String = "",
        emailMask: String = "",
        profileId: String = "",
        loginProvider: String = ""
    ) {
        self.authState = authState
        self.userId = userId
        self.anonymousId = anonymousId
        self.emailMask = emailMask
        self.profileId = profileId
        self.loginProvider = loginProvider
    }

    /// Allowed states. Anything else becomes `unknown`, and a raw email is cleared.
    public func normalized() -> CaptureSessionIdentity {
        let allowed: Set<String> = ["cold_start", "guest", "logged_in", "tve", "unknown"]
        let state = allowed.contains(authState) ? authState : "unknown"
        let dropUser = state == "guest" || state == "cold_start" || state == "unknown"
        return CaptureSessionIdentity(
            authState: state,
            userId: dropUser ? "" : userId,
            anonymousId: anonymousId,
            emailMask: Self.safeMask(emailMask),
            profileId: dropUser ? "" : profileId,
            loginProvider: loginProvider
        )
    }

    func eventPayload() -> [String: JSONValue] {
        let value = normalized()
        return [
            "auth_state": .string(value.authState),
            "user_id": .string(value.userId),
            "anonymous_id": .string(value.anonymousId),
            "email_mask": .string(value.emailMask),
            "profile_id": .string(value.profileId),
            "login_provider": .string(value.loginProvider)
        ]
    }

    private static func safeMask(_ raw: String) -> String {
        guard raw.contains("@") else { return raw }
        return raw.contains("***") ? raw : ""
    }

    enum CodingKeys: String, CodingKey {
        case authState = "auth_state"
        case userId = "user_id"
        case anonymousId = "anonymous_id"
        case emailMask = "email_mask"
        case profileId = "profile_id"
        case loginProvider = "login_provider"
    }
}

/// Remember who is signed in. The debug bridge sends a change on the session event channel.
public func reportCaptureSession(_ identity: CaptureSessionIdentity) {
    CaptureSessionStore.shared.report(identity)
}

final class CaptureSessionStore: @unchecked Sendable {
    static let shared = CaptureSessionStore()

    private let lock = NSLock()
    private var current: CaptureSessionIdentity?
    private var pending: CaptureSessionIdentity?

    func report(_ identity: CaptureSessionIdentity) {
        let next = identity.normalized()
        lock.lock()
        defer { lock.unlock() }
        if current == next { return }
        current = next
        pending = next
    }

    func currentIdentity() -> CaptureSessionIdentity? {
        lock.lock()
        defer { lock.unlock() }
        return current
    }

    func peekPending() -> CaptureSessionIdentity? {
        lock.lock()
        defer { lock.unlock() }
        return pending
    }

    func ack(_ sent: CaptureSessionIdentity) {
        lock.lock()
        defer { lock.unlock() }
        if pending == sent { pending = nil }
    }
}
