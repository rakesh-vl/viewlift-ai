import Foundation
import ObjectiveC.runtime

/// Installs CaptureURLProtocol into configurations created after capture starts.
///
/// URLSessionConfiguration returns a new instance from `default` and `ephemeral`.
/// Swizzling those factory getters is required for automatic interception. The
/// swizzle is installed once; CaptureURLProtocol declines requests while stopped.
enum ConfigurationInterceptor {
    private static let lock = NSLock()
    private static var factoriesSwizzled = false

    static func install() {
        lock.lock()
        defer { lock.unlock() }

        URLProtocol.registerClass(CaptureURLProtocol.self)
        guard !factoriesSwizzled else { return }

        swizzle(
            original: #selector(getter: URLSessionConfiguration.default),
            replacement: #selector(URLSessionConfiguration.viewliftDefault)
        )
        swizzle(
            original: #selector(getter: URLSessionConfiguration.ephemeral),
            replacement: #selector(URLSessionConfiguration.viewliftEphemeral)
        )
        factoriesSwizzled = true
    }

    static func unregisterSharedSession() {
        URLProtocol.unregisterClass(CaptureURLProtocol.self)
    }

    static func inject(into configuration: URLSessionConfiguration) {
        var classes = configuration.protocolClasses ?? []
        guard !classes.contains(where: { $0 == CaptureURLProtocol.self }) else { return }
        classes.insert(CaptureURLProtocol.self, at: 0)
        configuration.protocolClasses = classes
    }

    private static func swizzle(original: Selector, replacement: Selector) {
        guard
            let originalMethod = class_getClassMethod(URLSessionConfiguration.self, original),
            let replacementMethod = class_getClassMethod(
                URLSessionConfiguration.self,
                replacement
            )
        else {
            assertionFailure("URLSessionConfiguration interception is unavailable")
            return
        }
        method_exchangeImplementations(originalMethod, replacementMethod)
    }
}

private extension URLSessionConfiguration {
    @objc class func viewliftDefault() -> URLSessionConfiguration {
        let configuration = viewliftDefault()
        ConfigurationInterceptor.inject(into: configuration)
        return configuration
    }

    @objc class func viewliftEphemeral() -> URLSessionConfiguration {
        let configuration = viewliftEphemeral()
        ConfigurationInterceptor.inject(into: configuration)
        return configuration
    }
}
