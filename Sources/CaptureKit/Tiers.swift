import AppKit
import ApplicationServices

/// Result of one tier: text on success, otherwise a short machine-readable error. Tier 1 failures also
/// carry the raw `AXError` and the focused element's role, so a miss can be diagnosed from the log alone.
struct TierOutcome: Sendable, Equatable {
    var text: String?
    var error: String?
    var axError: Int?
    var role: String?

    static func success(_ text: String) -> TierOutcome { TierOutcome(text: text, error: nil) }
    static func failure(_ error: String, axError: AXError? = nil, role: String? = nil) -> TierOutcome {
        TierOutcome(text: nil, error: error, axError: axError.map { Int($0.rawValue) }, role: role)
    }
    var ok: Bool { text != nil }
}

/// The three capture tiers. Retry and timing policy live in `CaptureChain`; these are the raw operations.
@MainActor
enum Tiers {
    // MARK: Tier 1 — AX kAXSelectedText

    /// Reads `kAXSelectedText` from the focused element. `"empty"` means the attribute was readable but blank
    /// (no selection, or Chromium's tree still catching up); `"no-selected-text-attr"` means the element does
    /// not answer it at all — Safari's `AXWebArea` says `noValue` (−25212), Tk exposes no text element.
    static func tier1(focused lookup: FocusedLookup) -> TierOutcome {
        guard let focused = lookup.element else { return .failure("no-focused-element", axError: lookup.status) }
        let (value, status) = AX.stringWithStatus(focused, kAXSelectedTextAttribute)
        guard let text = value else {
            return .failure("no-selected-text-attr", axError: status, role: AX.string(focused, kAXRoleAttribute))
        }
        return text.isEmpty ? .failure("empty", role: AX.string(focused, kAXRoleAttribute)) : .success(text)
    }

    // MARK: Tier 2 — AX press on the app's Copy menu item

    /// Finds the menu item bound to plain ⌘C (`AXMenuItemCmdChar == "C"`, no extra modifiers) by walking the
    /// app's menu bar. The Edit menu is scanned first; other top-level menus only if it is not there.
    static func findCopyMenuItem(app: AXUIElement) -> AXUIElement? {
        guard let menuBar = AX.element(app, kAXMenuBarAttribute) else { return nil }
        let topLevel = AX.elements(menuBar, kAXChildrenAttribute)
        let edit = topLevel.filter { AX.string($0, kAXTitleAttribute) == "Edit" }
        let rest = topLevel.filter { AX.string($0, kAXTitleAttribute) != "Edit" }
        for barItem in edit + rest {
            for menu in AX.elements(barItem, kAXChildrenAttribute) {
                for item in AX.elements(menu, kAXChildrenAttribute) where isPlainCommandC(item) {
                    return item
                }
            }
        }
        return nil
    }

    private static func isPlainCommandC(_ item: AXUIElement) -> Bool {
        guard let char = AX.string(item, kAXMenuItemCmdCharAttribute), char.uppercased() == "C" else { return false }
        // 0 == kAXMenuItemModifierNone: the command key alone.
        return (AX.int(item, kAXMenuItemCmdModifiersAttribute) ?? 0) == 0
    }

    /// `AXEnabled` of the Copy item. `nil` when the element is no longer valid (menu rebuilt).
    static func copyItemEnabled(_ item: AXUIElement) -> Bool? {
        AX.bool(item, kAXEnabledAttribute)
    }

    /// Presses the Copy menu item and waits for the pasteboard to change.
    static func tier2(
        copyItem: AXUIElement, pasteboard: NSPasteboard, lateCopyGuard: PasteboardGuard, timeout: Duration, grace: Duration
    ) async -> TierOutcome {
        await viaClipboard(pasteboard: pasteboard, lateCopyGuard: lateCopyGuard, timeout: timeout, grace: grace, failure: "press-failed") {
            AX.perform(copyItem, action: kAXPressAction)
        }
    }

    // MARK: Tier 3 — synthetic ⌘C

    /// Posts ⌘C (virtual key 8) through the HID event tap and waits for the pasteboard to change.
    static func tier3(pasteboard: NSPasteboard, lateCopyGuard: PasteboardGuard, timeout: Duration, grace: Duration) async -> TierOutcome {
        await viaClipboard(pasteboard: pasteboard, lateCopyGuard: lateCopyGuard, timeout: timeout, grace: grace, failure: "post-failed") {
            guard let down = CGEvent(keyboardEventSource: nil, virtualKey: 8, keyDown: true),
                  let up = CGEvent(keyboardEventSource: nil, virtualKey: 8, keyDown: false)
            else { return false }
            down.flags = .maskCommand
            up.flags = .maskCommand
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
            return true
        }
    }

    // MARK: Shared clipboard protocol

    /// Snapshot → act → wait for `changeCount` to advance → read → restore whenever the pasteboard changed
    /// (a timeout or failed action that never touched it is left untouched) → arm the late-copy guard, because
    /// a Copy that lands after the timeout, or a second write after a hit, would otherwise survive the restore.
    private static func viaClipboard(
        pasteboard: NSPasteboard,
        lateCopyGuard: PasteboardGuard,
        timeout: Duration,
        grace: Duration,
        failure: String,
        action: () -> Bool
    ) async -> TierOutcome {
        lateCopyGuard.settle()
        let snapshot = PasteboardSnapshot.take(from: pasteboard)
        let outcome = await attempt(snapshot: snapshot, pasteboard: pasteboard, timeout: timeout, failure: failure, action: action)
        snapshot.restoreIfChanged(to: pasteboard)
        if outcome.error != failure {
            // The action reached the app, so its Copy may still be on its way.
            lateCopyGuard.arm(snapshot: snapshot, capturedText: outcome.text, grace: grace)
        }
        return outcome
    }

    private static func attempt(
        snapshot: PasteboardSnapshot, pasteboard: NSPasteboard, timeout: Duration, failure: String, action: () -> Bool
    ) async -> TierOutcome {
        guard action() else { return .failure(failure) }
        guard await PasteboardWait.forChange(on: pasteboard, from: snapshot.changeCount, timeout: timeout) else {
            return .failure("timeout")
        }
        guard let text = pasteboard.string(forType: .string) else { return .failure("no-text") }
        return text.isEmpty ? .failure("empty") : .success(text)
    }
}
