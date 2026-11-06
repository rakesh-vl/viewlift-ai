import CommonCrypto
import Foundation
import OSLog
import Security

/// Trust is the dashboard's exact self-signed leaf certificate, compared by SHA-256.
extension PinnedHTTPSTransport {
    public func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        let expected: String?
        lock.lock()
        expected = configuration?.pinnedCertificateSHA256.lowercased()
        lock.unlock()
        guard let expected else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        if evaluatePin(trust: trust, expectedSHA256Hex: expected) {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            Self.log.error("Receiver certificate does not match pinned SHA-256. Update the fingerprint.")
            completionHandler(.cancelAuthenticationChallenge, nil)
        }
    }

    func evaluatePin(trust: SecTrust, expectedSHA256Hex: String) -> Bool {
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
              let leaf = chain.first else {
            return false
        }
        let data = SecCertificateCopyData(leaf) as Data
        let normalized = expectedSHA256Hex
            .replacingOccurrences(of: ":", with: "")
            .replacingOccurrences(of: " ", with: "")
            .lowercased()
        // Exact leaf pin authenticates the dashboard's self-signed certificate.
        // Requiring system trust here would reject that certificate before pin comparison.
        return sha256Hex(data) == normalized
    }

    private func sha256Hex(_ data: Data) -> String {
        var hash = [UInt8](repeating: 0, count: Int(CC_SHA256_DIGEST_LENGTH))
        data.withUnsafeBytes { buffer in
            _ = CC_SHA256(buffer.baseAddress, CC_LONG(buffer.count), &hash)
        }
        return hash.map { String(format: "%02x", $0) }.joined()
    }
}
