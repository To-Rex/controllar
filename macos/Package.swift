// swift-tools-version:5.9
import Foundation
import PackageDescription

// Absolute path to the prebuilt Rust static library (see scripts/build-macos.sh).
let packageDir = URL(fileURLWithPath: #filePath).deletingLastPathComponent().path
let vendorLibDir = "\(packageDir)/Vendor"

let package = Package(
    name: "FlowLink",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "FlowLink", targets: ["FlowLink"])
    ],
    targets: [
        // C ABI header + stub source so SwiftPM creates a module map for the
        // Rust core (linked as libflowlink_ffi.a, see the executable target).
        .target(
            name: "CFlowLinkCore",
            path: "Sources/CFlowLinkCore",
            publicHeadersPath: "include"
        ),
        .target(
            name: "FlowLinkKit",
            dependencies: ["CFlowLinkCore"],
            path: "Sources/FlowLinkKit"
        ),
        .executableTarget(
            name: "FlowLink",
            dependencies: ["FlowLinkKit"],
            path: "Sources/FlowLink",
            linkerSettings: [
                .unsafeFlags(["-L", vendorLibDir]),
                .linkedLibrary("flowlink_ffi"),
            ]
        ),
        .testTarget(
            name: "FlowLinkTests",
            dependencies: ["FlowLinkKit"],
            path: "Tests/FlowLinkTests",
            linkerSettings: [
                .unsafeFlags(["-L", vendorLibDir]),
                .linkedLibrary("flowlink_ffi"),
            ]
        ),
    ]
)
