// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "ViewliftNetworkCapture",
    platforms: [
        .iOS(.v16),
        .tvOS(.v16),
        // Host unit tests via `swift test` (library remains iOS/tvOS-first).
        .macOS(.v13)
    ],
    products: [
        .library(
            name: "ViewliftNetworkCapture",
            targets: ["ViewliftNetworkCapture"]
        )
    ],
    targets: [
        .target(
            name: "ViewliftNetworkCapture",
            path: "Sources/ViewliftNetworkCapture"
        ),
        .testTarget(
            name: "ViewliftNetworkCaptureTests",
            dependencies: ["ViewliftNetworkCapture"],
            path: "Tests/ViewliftNetworkCaptureTests"
        )
    ]
)
