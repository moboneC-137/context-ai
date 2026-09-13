import AppKit
import Testing
@testable import CaptureKit

/// The late-copy guard on a private pasteboard, with the hardware-input check scripted.
@Suite("PasteboardGuard", .serialized)
@MainActor
struct PasteboardGuardTests {
    private func makePasteboard() -> NSPasteboard {
        NSPasteboard(name: NSPasteboard.Name("com.contextai.capturekit.guard.\(UUID().uuidString)"))
    }

    /// Pasteboard holding "original", its snapshot, and a guard whose input check returns `userInput`.
    private func armed(userInput: Bool, capturedText: String?, grace: Duration = .milliseconds(300))
        -> (NSPasteboard, PasteboardSnapshot, PasteboardGuard)
    {
        let pasteboard = makePasteboard()
        pasteboard.clearContents()
        pasteboard.setString("original", forType: .string)
        let snapshot = PasteboardSnapshot.take(from: pasteboard)
        let lateCopyGuard = PasteboardGuard(pasteboard: pasteboard, inputSince: { _ in userInput }, pollInterval: .milliseconds(5))
        lateCopyGuard.arm(snapshot: snapshot, capturedText: capturedText, grace: grace)
        return (pasteboard, snapshot, lateCopyGuard)
    }

    private func lateCopy(_ text: String?, on pasteboard: NSPasteboard) {
        pasteboard.clearContents()
        if let text { pasteboard.setString(text, forType: .string) } else { pasteboard.setData(Data([1, 2, 3]), forType: .pdf) }
    }

    @Test("a late copy of the captured text with no user input is reverted, and the guard keeps watching")
    func revertsLateCopy() {
        let (pasteboard, _, lateCopyGuard) = armed(userInput: false, capturedText: "selection")
        defer { pasteboard.releaseGlobally() }
        lateCopy("selection", on: pasteboard)
        #expect(lateCopyGuard.check() == true)
        #expect(pasteboard.string(forType: .string) == "original")
        #expect(lateCopyGuard.restoredLateCopies == 1)
        #expect(lateCopyGuard.isArmed)
        // An unchanged pasteboard is left alone.
        #expect(lateCopyGuard.check() == false)
        // IntelliJ writes twice: the second late write is reverted as well.
        lateCopy("selection", on: pasteboard)
        #expect(lateCopyGuard.check() == true)
        #expect(pasteboard.string(forType: .string) == "original")
        #expect(lateCopyGuard.restoredLateCopies == 2)
    }

    @Test("the polling task reverts a late copy on its own")
    func polls() async {
        let (pasteboard, _, lateCopyGuard) = armed(userInput: false, capturedText: nil, grace: .seconds(2))
        defer { pasteboard.releaseGlobally() }
        lateCopy("late", on: pasteboard)
        let clock = ContinuousClock()
        let deadline = clock.now + .seconds(1)
        while lateCopyGuard.restoredLateCopies == 0, clock.now < deadline {
            try? await Task.sleep(for: .milliseconds(10))
        }
        #expect(lateCopyGuard.restoredLateCopies == 1)
        #expect(pasteboard.string(forType: .string) == "original")
        lateCopyGuard.settle()
    }

    @Test("a non-text late write after a timeout (no captured text) is reverted")
    func revertsNonText() {
        let (pasteboard, _, lateCopyGuard) = armed(userInput: false, capturedText: nil)
        defer { pasteboard.releaseGlobally() }
        lateCopy(nil, on: pasteboard)
        #expect(lateCopyGuard.check() == true)
        #expect(pasteboard.string(forType: .string) == "original")
        #expect(lateCopyGuard.restoredLateCopies == 1)
    }

    @Test("a change after hardware input is the user's: left alone, guard disarmed")
    func respectsUserCopy() {
        let (pasteboard, _, lateCopyGuard) = armed(userInput: true, capturedText: "selection")
        defer { pasteboard.releaseGlobally() }
        lateCopy("selection", on: pasteboard)
        #expect(lateCopyGuard.check() == false)
        #expect(pasteboard.string(forType: .string) == "selection")
        #expect(lateCopyGuard.restoredLateCopies == 0)
        #expect(!lateCopyGuard.isArmed)
    }

    @Test("text that differs from the captured text is foreign: left alone, guard disarmed")
    func respectsForeignText() {
        let (pasteboard, _, lateCopyGuard) = armed(userInput: false, capturedText: "selection")
        defer { pasteboard.releaseGlobally() }
        lateCopy("something else", on: pasteboard)
        #expect(lateCopyGuard.check() == false)
        #expect(pasteboard.string(forType: .string) == "something else")
        #expect(!lateCopyGuard.isArmed)
    }

    @Test("no change: nothing is written and the guard disarms at the deadline")
    func quietGrace() async {
        let (pasteboard, _, lateCopyGuard) = armed(userInput: false, capturedText: "x", grace: .milliseconds(30))
        defer { pasteboard.releaseGlobally() }
        let count = pasteboard.changeCount
        await lateCopyGuard.waitUntilSettled()
        #expect(pasteboard.changeCount == count)
        #expect(!lateCopyGuard.isArmed)
        #expect(lateCopyGuard.restoredLateCopies == 0)
    }

    @Test("settle performs a final check and stops watching")
    func settle() {
        let (pasteboard, _, lateCopyGuard) = armed(userInput: false, capturedText: nil)
        defer { pasteboard.releaseGlobally() }
        lateCopy("late", on: pasteboard)
        lateCopyGuard.settle()
        #expect(pasteboard.string(forType: .string) == "original")
        #expect(!lateCopyGuard.isArmed)
        #expect(lateCopyGuard.restoredLateCopies == 1)
    }
}
