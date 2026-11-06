// swift-tools-version: 5.9
import PackageDescription

/// Root package manifest so AppCMS can depend on ViewliftNetworkCapture via
/// Git SPM (`https://github.com/rakesh-vl/viewlift-ai.git`). Local package
/// development still uses `ios/ViewliftNetworkCapture/Package.swift`.
let package = Package(
    name: "ViewliftNetworkCapture",
    platforms: [
        .iOS(.v15),
        .tvOS(.v16),
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
            path: "ios/ViewliftNetworkCapture/Sources/ViewliftNetworkCapture",
            // Xcode custom configurations (for example "Dev") do not give package targets DEBUG.
            // Hosts still start capture only under #if DEBUG.
            swiftSettings: [.define("VIEWLIFT_CAPTURE_ENABLED")]
        ),
        .testTarget(
            name: "ViewliftNetworkCaptureTests",
            dependencies: ["ViewliftNetworkCapture"],
            path: "ios/ViewliftNetworkCapture/Tests/ViewliftNetworkCaptureTests"
        )
    ]
)
