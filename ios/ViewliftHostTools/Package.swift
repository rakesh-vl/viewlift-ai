// swift-tools-version:5.9
import PackageDescription

// Mac-side helpers the Viewlift AI console builds on demand. Not linked into any app.
let package = Package(
    name: "ViewliftHostTools",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "viewlift-screen-recorder", targets: ["ScreenRecorder"])
    ],
    targets: [
        .executableTarget(
            name: "ScreenRecorder",
            path: "Sources/ScreenRecorder",
            linkerSettings: [
                .linkedFramework("AVFoundation"),
                .linkedFramework("CoreMediaIO")
            ]
        )
    ]
)
