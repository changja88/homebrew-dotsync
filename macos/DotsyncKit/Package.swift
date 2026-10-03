// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "DotsyncKit",
    platforms: [.macOS("27.0")],
    products: [.library(name: "DotsyncKit", type: .static, targets: ["DotsyncKit"])],
    targets: [
        .target(name: "DotsyncKit"),
        .testTarget(name: "DotsyncKitTests", dependencies: ["DotsyncKit"]),
    ],
    swiftLanguageModes: [.v5]
)
