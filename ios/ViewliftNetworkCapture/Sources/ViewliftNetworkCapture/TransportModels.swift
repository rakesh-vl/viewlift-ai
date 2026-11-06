import Foundation

struct PairResponse: Decodable {
    let sessionToken: String
    enum CodingKeys: String, CodingKey {
        case sessionToken = "session_token"
    }
}

struct DebugPairResponse: Decodable {
    let pairingToken: String
    enum CodingKeys: String, CodingKey {
        case pairingToken = "pairing_token"
    }
}

enum TransportError: Error {
    case notStarted
    case notPaired
    case missingCredentials
    case pairingFailed(status: Int)
    case uploadFailed(status: Int)

    /// The transport is starting or pairing. Retry soon; this is not a connection failure.
    var isWaitingForSession: Bool {
        switch self {
        case .notStarted, .notPaired:
            return true
        default:
            return false
        }
    }
}
