import AppKit
import CaptureKit
import Darwin
import Foundation

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

// MARK: - Session

@MainActor
final class Session {
    let arguments: Arguments
    let output: Output
    let chain = CaptureChain()
    private var gate = SelectionGate()
    private var results: [CaptureResult] = []
    private var capturing = false
    private var finishRequested = false
    private var skippedWhileCapturing = 0
    private var monitors: [Any] = []

    init(arguments: Arguments, output: Output) {
        self.arguments = arguments
        self.output = output
    }

    func runOnce() async -> Int32 {
        let result = await chain.capture(options: arguments.captureOptions)
        output.emit(result.truncatingText(to: arguments.maxText).jsonLine())
        // A Copy that lands after a clipboard tier's timeout must be reverted before this process is gone.
        await chain.settleClipboard()
        chain.resetEnhancedAX()
        output.close()
        return result.isHit ? 0 : 1
    }

    func startMonitoring() {
        let down = NSEvent.addGlobalMonitorForEvents(matching: .leftMouseDown) { _ in
            let point = NSEvent.mouseLocation
            Task { @MainActor in self.gate.mouseDown(at: point) }
        }
        let up = NSEvent.addGlobalMonitorForEvents(matching: .leftMouseUp) { event in
            let point = NSEvent.mouseLocation
            let clicks = event.clickCount
            Task { @MainActor in self.mouseUp(at: point, clickCount: clicks) }
        }
        monitors = [down, up].compactMap { $0 }
        if monitors.count < 2 {
            stderr("capture-spike: could not install the global mouse monitor")
            exit(2)
        }
    }

    private func mouseUp(at point: CGPoint, clickCount: Int) {
        guard gate.mouseUp(at: point, clickCount: clickCount, now: .now) else { return }
        guard !finishRequested else { return }
        guard !capturing else { skippedWhileCapturing += 1; return }
        capturing = true
        Task { @MainActor in
            let result = await chain.capture(options: arguments.captureOptions)
            results.append(result)
            output.emit(result.truncatingText(to: arguments.maxText).jsonLine())
            capturing = false
        }
    }

    /// Prints the per-app summary and terminates the process. A capture in flight is allowed to finish
    /// (so its clipboard restore runs) and the late-copy guard to settle (at most the grace period) first;
    /// a second signal forces the exit.
    func finish() {
        if finishRequested {
            stderr("capture-spike: forced exit; the clipboard may not have been restored")
            exit(130)
        }
        finishRequested = true
        if capturing {
            stderr("capture-spike: finishing the capture in flight…")
        } else if chain.lateCopyGuard?.isArmed ?? false {
            stderr("capture-spike: waiting for the clipboard to settle…")
        }
        Task { @MainActor in
            while capturing { try? await Task.sleep(for: .milliseconds(20)) }
            await chain.settleClipboard()
            chain.resetEnhancedAX()
            output.close()
            stderr("")
            stderr(Stats.renderTable(Stats.summarize(results)))
            if skippedWhileCapturing > 0 {
                stderr("\(skippedWhileCapturing) gesture\(skippedWhileCapturing == 1 ? "" : "s") skipped while a capture was in flight")
            }
            if let reverted = chain.lateCopyGuard?.restoredLateCopies, reverted > 0 {
                stderr("\(reverted) late cop\(reverted == 1 ? "y" : "ies") reverted after a clipboard tier had finished")
            }
            exit(0)
        }
    }
}

// MARK: - Entry point

// A closed stdout (e.g. `| head`) must not kill the process before the clipboard restore and summary run.
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

let output: Output
do {
    output = try Output(path: arguments.outPath)
} catch {
    stderr("capture-spike: cannot open --out file: \(error.localizedDescription)")
    exit(64)
}

let session = Session(arguments: arguments, output: output)

if arguments.once {
    Task { @MainActor in
        let code = await session.runOnce()
        exit(code)
    }
    RunLoop.main.run()
} else {
    // Headless: the NSApplication exists only to host the global event monitor.
    let app = NSApplication.shared
    app.setActivationPolicy(.prohibited)

    signal(SIGINT, SIG_IGN)
    signal(SIGTERM, SIG_IGN)
    let sigint = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
    let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
    for source in [sigint, sigterm] {
        source.setEventHandler { MainActor.assumeIsolated { session.finish() } }
        source.resume()
    }

    session.startMonitoring()
    stderr("capture-spike: listening (tiers \(arguments.tiers.map(String.init).joined(separator: ",")), enhanced AX \(arguments.enhancedAX ? "on" : "off")). Select text in any app; Ctrl-C for the summary."
        + (arguments.outPath.map { " Appending JSON lines to \($0)." } ?? ""))
    app.run()
}
