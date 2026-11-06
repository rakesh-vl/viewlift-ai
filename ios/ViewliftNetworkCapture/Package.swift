// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "ViewliftNetworkCapture",
    platforms: [
        .iOS(.v15),
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
            path: "Sources/ViewliftNetworkCapture",
            // Xcode custom configurations (for example "Dev") do not give package targets DEBUG.
            // Hosts still start capture only under #if DEBUG.
            swiftSettings: [.define("VIEWLIFT_CAPTURE_ENABLED")]
        ),
        .testTarget(
            name: "ViewliftNetworkCaptureTests",
            dependencies: ["ViewliftNetworkCapture"],
            path: "Tests/ViewliftNetworkCaptureTests"
        )
    ]
)
