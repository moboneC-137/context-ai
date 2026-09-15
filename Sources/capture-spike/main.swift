import AppKit
import CaptureKit
import Darwin
import Foundation

// The native capture primitive behind the Swift/Python contract (docs/capture-contract.md v1):
// parse flags → trust check → run the chain once on the frontmost app → print one JSON line → let the
// late-copy guard settle → exit. Gesture detection, statistics, persistence and everything above the
// line live in the Python package (migration step 4, 2026-09-14).

func stderr(_ message: String) {
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

// MARK: - Host application (what TCC actually attributes this process to)

enum HostApp {
    /// Walks up the process tree to the first ancestor that is a GUI application — the terminal.
    static func name() -> String {
        var pid = getpid()
        for _ in 0..<16 {
            guard let parent = parentPid(of: pid), parent > 0 else { break }
            pid = parent
            if let app = NSRunningApplication(processIdentifier: pid), app.bundleIdentifier != nil,
               let name = app.localizedName
            {
                return name
            }
        }
        switch ProcessInfo.processInfo.environment["TERM_PROGRAM"] {
        case "Apple_Terminal": return "Terminal"
        case "iTerm.app": return "iTerm2"
        case "vscode": return "Visual Studio Code"
        case "WarpTerminal": return "Warp"
        case let other?: return other
        case nil: return "the terminal application that launched this tool"
        }
    }

    private static func parentPid(of pid: pid_t) -> pid_t? {
        var info = kinfo_proc()
        var size = MemoryLayout<kinfo_proc>.stride
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid]
        guard sysctl(&mib, UInt32(mib.count), &info, &size, nil, 0) == 0, size > 0 else { return nil }
        return info.kp_eproc.e_ppid
    }
}

// MARK: - Entry point

// A closed stdout (e.g. `| head`) must not kill the process before the clipboard restore runs.
signal(SIGPIPE, SIG_IGN)

let arguments: Arguments
switch Arguments.parse(Array(CommandLine.arguments.dropFirst())) {
case .success(let parsed):
    arguments = parsed
case .failure(let usageError):
    stderr("capture-spike: \(usageError.message)")
    stderr(Arguments.usage)
    exit(64)
}

if arguments.help {
    print(Arguments.usage)
    exit(0)
}

if !AccessibilityTrust.isTrusted() {
    AccessibilityTrust.promptIfNeeded()
    let host = HostApp.name()
    stderr("""
    capture-spike: Accessibility access is not granted.
    macOS attributes a command-line tool to the app that launched it, so the app that needs permission is:

        \(host)

    Open System Settings > Privacy & Security > Accessibility, enable "\(host)", then run capture-spike again.
    """)
    exit(2)
}

Task { @MainActor in
    let chain = CaptureChain()
    let result = await chain.capture(options: arguments.captureOptions)
    // The line goes out — flushed — before the guard wait, so the caller never waits on it.
    print(result.truncatingText(to: arguments.maxText).jsonLine())
    fflush(stdout)
    // A Copy that lands after a clipboard tier's timeout must be reverted before this process is gone.
    await chain.settleClipboard()
    chain.resetEnhancedAX()
    exit(result.isHit ? 0 : 1)
}
RunLoop.main.run()
