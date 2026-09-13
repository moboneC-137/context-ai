// swift-tools-version: 6.0
import Foundation
import PackageDescription

// With only the Command Line Tools installed (no Xcode), SwiftPM cannot locate Swift Testing by itself
// because it derives the platform frameworks directory from an Xcode platform path. When the active
// developer directory is the CLT, point the test target at the CLT's copy so plain `swift test` works.
let commandLineTools = "/Library/Developer/CommandLineTools"
let cltFrameworks = commandLineTools + "/Library/Developer/Frameworks"
let cltTestingLib = commandLineTools + "/Library/Developer/usr/lib"

let activeDeveloperDir: String = {
    if let explicit = ProcessInfo.processInfo.environment["DEVELOPER_DIR"] { return explicit }
    if let link = try? FileManager.default.destinationOfSymbolicLink(atPath: "/private/var/db/xcode_select_link") {
        return link
    }
    return commandLineTools // xcode-select's own fallback when no link exists
}()
let needsCLTTestingPaths = activeDeveloperDir.hasPrefix(commandLineTools)
    && FileManager.default.fileExists(atPath: cltFrameworks + "/Testing.framework")

let testSwiftSettings: [SwiftSetting] = needsCLTTestingPaths ? [.unsafeFlags(["-F", cltFrameworks])] : []
let testLinkerSettings: [LinkerSetting] = needsCLTTestingPaths
    ? [.unsafeFlags(["-F", cltFrameworks, "-Xlinker", "-rpath", "-Xlinker", cltFrameworks, "-Xlinker", "-rpath", "-Xlinker", cltTestingLib])]
    : []

let package = Package(
    name: "context-ai",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "CaptureKit", targets: ["CaptureKit"]),
        .executable(name: "capture-spike", targets: ["capture-spike"]),
    ],
    targets: [
        .target(name: "CaptureKit"),
        .executableTarget(name: "capture-spike", dependencies: ["CaptureKit"]),
        .testTarget(
            name: "CaptureKitTests",
            dependencies: ["CaptureKit"],
            swiftSettings: testSwiftSettings,
            linkerSettings: testLinkerSettings
        ),
        .testTarget(
            name: "CaptureSpikeTests",
            dependencies: ["capture-spike"],
            swiftSettings: testSwiftSettings,
            linkerSettings: testLinkerSettings
        ),
    ]
)
