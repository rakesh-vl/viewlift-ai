import Foundation

/// Copies a URLProtocol request body into `httpBody` so capture and replay share one buffer.
///
/// URL loading moves `httpBody` onto `httpBodyStream` before `URLProtocol` sees the request.
/// GraphQL clients hit that path. Reading the stream consumes it, so the replay request must
/// take the buffered bytes or the app sends an empty POST.
enum RequestBodyBuffer {
    /// File uploads above this stay on the original stream and are recorded empty.
    static let replayCap = 2 * 1024 * 1024

    static func apply(_ request: inout URLRequest) -> Data {
        if request.httpBodyStream == nil {
            return request.httpBody ?? Data()
        }
        if let length = contentLength(request), length > replayCap {
            return request.httpBody ?? Data()
        }
        guard let stream = request.httpBodyStream else {
            return request.httpBody ?? Data()
        }
        let buffered = read(stream, maxBytes: replayCap)
        request.httpBodyStream = nil
        if let buffered, !buffered.isEmpty {
            request.httpBody = buffered
        }
        return request.httpBody ?? Data()
    }

    private static func contentLength(_ request: URLRequest) -> Int? {
        guard let raw = request.value(forHTTPHeaderField: "Content-Length") else {
            return nil
        }
        return Int(raw)
    }

    private static func read(_ stream: InputStream, maxBytes: Int) -> Data? {
        stream.open()
        defer { stream.close() }
        var data = Data()
        let chunk = 16 * 1024
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: chunk)
        defer { buffer.deallocate() }
        while data.count < maxBytes {
            let count = stream.read(buffer, maxLength: min(chunk, maxBytes - data.count))
            if count < 0 {
                return nil
            }
            if count == 0 {
                break
            }
            data.append(buffer, count: count)
        }
        return data
    }
}
