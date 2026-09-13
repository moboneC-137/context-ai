import AppKit
import ApplicationServices

/// Result of one tier: text on success, otherwise a short machine-readable error.
struct TierOutcome: Sendable, Equatable {
    var text: String?
    var error: String?

    static func success(_ text: String) -> TierOutcome { TierOutcome(text: text, error: nil) }
    static func failure(_ error: String) -> TierOutcome { TierOutcome(text: nil, error: error) }
    var ok: Bool { text != nil }
}

/// The three capture tiers. Retry and timing policy live in `CaptureChain`; these are the raw operations.
@MainActor
enum Tiers {
    // MARK: Tier 1 — AX kAXSelectedText

    /// Reads `kAXSelectedText` from the focused element. `"empty"` means the attribute was readable but blank,
    /// which is the signal Chromium gives while its accessibility tree is still being built.
    static func tier1(focused: AXUIElement?) -> TierOutcome {
        guard let focused else { return .failure("no-focused-element") }
        guard let text = AX.string(focused, kAXSelectedTextAttribute) else { return .failure("no-selected-text-attr") }
        return text.isEmpty ? .failure("empty") : .success(text)
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
    static func tier2(copyItem: AXUIElement, pasteboard: NSPasteboard, timeout: Duration) async -> TierOutcome {
        await viaClipboard(pasteboard: pasteboard, timeout: timeout, failure: "press-failed") {
            AX.perform(copyItem, action: kAXPressAction)
        }
    }

    // MARK: Tier 3 — synthetic ⌘C

    /// Posts ⌘C (virtual key 8) through the HID event tap and waits for the pasteboard to change.
    static func tier3(pasteboard: NSPasteboard, timeout: Duration) async -> TierOutcome {
        await viaClipboard(pasteboard: pasteboard, timeout: timeout, failure: "post-failed") {
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

    /// Snapshot → act → wait for `changeCount` to advance → read → restore via `defer` whenever the
    /// pasteboard changed (a timeout or failed action that never touched it is left untouched).
    private static func viaClipboard(
        pasteboard: NSPasteboard,
        timeout: Duration,
        failure: String,
        action: () -> Bool
    ) async -> TierOutcome {
        let snapshot = PasteboardSnapshot.take(from: pasteboard)
        defer { snapshot.restoreIfChanged(to: pasteboard) }

        guard action() else { return .failure(failure) }
        guard await PasteboardWait.forChange(on: pasteboard, from: snapshot.changeCount, timeout: timeout) else {
            return .failure("timeout")
        }
        guard let text = pasteboard.string(forType: .string) else { return .failure("no-text") }
        return text.isEmpty ? .failure("empty") : .success(text)
    }
}
