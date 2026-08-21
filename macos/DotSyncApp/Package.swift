// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "DotSyncApp",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "DotSyncNative", targets: ["DotSyncNative"]),
    ],
    dependencies: [],
    targets: [
        .target(name: "DotSyncNative"),
        .testTarget(
            name: "DotSyncNativeTests",
            dependencies: ["DotSyncNative"]
        ),
    ]
)
