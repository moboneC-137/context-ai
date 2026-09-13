import AppKit
import CoreGraphics

/// Watches the pasteboard for a short grace period after a clipboard tier has finished, and undoes a
/// *late* copy: a target app whose Copy lands after the tier's timeout (Preview renders the page, IntelliJ
/// writes twice) would otherwise leave the user's selection on the clipboard after the restore has run.
///
/// A change is reverted only when nothing else can explain it: no hardware key or mouse-down since the
/// guard was armed (the user's own ⌘C or Edit > Copy always wins), and — when the tier captured text —
/// the new pasteboard string is that same text. Everything else is left exactly as found.
@MainActor
public final class PasteboardGuard {
    /// Answers whether hardware input happened after `instant`. Injectable for tests.
    typealias InputCheck = @MainActor (_ since: ContinuousClock.Instant) -> Bool

    private let pasteboard: NSPasteboard
    private let inputSince: InputCheck
    private let pollInterval: Duration
    private let clock = ContinuousClock()
    private var task: Task<Void, Never>?
    private var pending: Pending?

    private struct Pending {
        var snapshot: PasteboardSnapshot
        var expectedChangeCount: Int
        var capturedText: String?
        var armedAt: ContinuousClock.Instant
        var deadline: ContinuousClock.Instant
    }

    /// Count of late copies this guard reverted; the CLI prints it with the summary.
    public private(set) var restoredLateCopies = 0

    public convenience init(pasteboard: NSPasteboard = .general) {
        self.init(pasteboard: pasteboard, inputSince: PasteboardGuard.hardwareInputSince, pollInterval: .milliseconds(50))
    }

    init(pasteboard: NSPasteboard, inputSince: @escaping InputCheck, pollInterval: Duration) {
        self.pasteboard = pasteboard
        self.inputSince = inputSince
        self.pollInterval = pollInterval
    }

    /// Whether a grace period is currently running.
    public var isArmed: Bool { pending != nil }

    /// Starts (or restarts) the grace period. Call after the tier's own restore, so `pasteboard.changeCount`
    /// is the value the clipboard should keep.
    func arm(snapshot: PasteboardSnapshot, capturedText: String?, grace: Duration) {
        task?.cancel()
        let now = clock.now
        pending = Pending(
            snapshot: snapshot,
            expectedChangeCount: pasteboard.changeCount,
            capturedText: capturedText,
            armedAt: now,
            deadline: now + grace
        )
        task = Task { @MainActor [weak self] in
            while let self, !Task.isCancelled, let pending = self.pending {
                if self.clock.now >= pending.deadline { self.pending = nil; return }
                try? await Task.sleep(for: self.pollInterval)
                self.check()
            }
        }
    }

    /// One inspection of the pasteboard: reverts a late copy, or disarms when the change is not ours.
    @discardableResult
    func check() -> Bool {
        guard var pending else { return false }
        guard pasteboard.changeCount != pending.expectedChangeCount else { return false }
        // Any key or mouse-down since arming means the user (or another tool) owns the clipboard now.
        if inputSince(pending.armedAt) {
            disarm()
            return false
        }
        if let captured = pending.capturedText, let current = pasteboard.string(forType: .string), current != captured {
            disarm()
            return false
        }
        pending.snapshot.restore(to: pasteboard)
        pending.expectedChangeCount = pasteboard.changeCount
        self.pending = pending
        restoredLateCopies += 1
        return true
    }

    /// Final inspection then stop watching. Use before another clipboard tier snapshots the pasteboard.
    func settle() {
        check()
        disarm()
    }

    /// Waits for the grace period to end (or the guard to disarm), so a process can exit knowing that a
    /// late copy has been handled.
    public func waitUntilSettled() async {
        while let pending {
            if clock.now >= pending.deadline { settle(); return }
            try? await Task.sleep(for: pollInterval)
        }
    }

    private func disarm() {
        task?.cancel()
        task = nil
        pending = nil
    }

    /// Hardware (HID) key-down or mouse-down more recent than `instant`. Events the chain posts itself
    /// happen before the guard is armed, so they never count.
    static func hardwareInputSince(_ instant: ContinuousClock.Instant) -> Bool {
        let elapsed = ContinuousClock().now - instant
        let (seconds, attoseconds) = elapsed.components
        let elapsedSeconds = Double(seconds) + Double(attoseconds) / 1e18
        let types: [CGEventType] = [.keyDown, .leftMouseDown, .rightMouseDown, .otherMouseDown]
        return types.contains { type in
            CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: type) < elapsedSeconds
        }
    }
}
