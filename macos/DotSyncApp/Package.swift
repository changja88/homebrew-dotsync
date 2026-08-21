// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "DotSyncApp",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "DotSyncNative", targets: ["DotSyncNative"]),
        .executable(name: "DotSync", targets: ["DotSyncApp"]),
    ],
    dependencies: [],
    targets: [
        .target(name: "DotSyncNative"),
        .executableTarget(
            name: "DotSyncApp",
            dependencies: ["DotSyncNative"]
        ),
        .testTarget(
            name: "DotSyncNativeTests",
            dependencies: ["DotSyncNative", "DotSyncApp"]
        ),
    ]
)
