import AppKit
import ApplicationServices

/// Pure validation and coordinate conversion for rectangles that come out of the accessibility API.
/// AX reports rects with a top-left origin (y grows downward from the top of the primary display);
/// `NSScreen`/`NSEvent` use a bottom-left origin. Everything published in `CaptureResult` is bottom-left.
public enum AXRect {
    /// Converts an AX (top-left) rect to `NSScreen` (bottom-left) coordinates and rejects garbage.
    ///
    /// - Parameters:
    ///   - rect: The rect as returned by AX.
    ///   - screens: Frames of all displays in `NSScreen` coordinates; the first is the primary display.
    /// - Returns: The flipped rect, or `nil` when it is empty, non-finite, or off every screen
    ///   (the y ≈ −9800 values Google Sheets and some web apps report land here).
    public static func flipAndValidate(_ rect: CGRect, screens: [CGRect]) -> Bounds? {
        guard let primary = screens.first else { return nil }
        let values = [rect.origin.x, rect.origin.y, rect.width, rect.height]
        guard values.allSatisfy(\.isFinite) else { return nil }
        guard rect.width > 0, rect.height > 0 else { return nil }

        let flipped = CGRect(
            x: rect.origin.x,
            y: primary.maxY - rect.maxY,
            width: rect.width,
            height: rect.height
        )
        guard screens.contains(where: { $0.intersects(flipped) }) else { return nil }
        return Bounds(flipped)
    }

    /// Largest share of a display a rect may cover and still count as a selection's bounds. WebKit, PDFKit,
    /// Chromium and Tk answer `AXFrame` with the whole document or window, which is "valid" but useless for
    /// positioning a panel; the mouse location is better than that.
    public static let maxScreenFraction = 0.25

    /// Whether `bounds` (bottom-left coordinates) can plausibly be a selection rect rather than a container.
    ///
    /// `AXBoundsForRange` rects (`fromFrame == false`) only have to fit on a display: a 20-line selection is
    /// legitimately large, and a drag can end past the last character, so the pointer is not required inside.
    /// `AXFrame` fallbacks (`fromFrame == true`) must cover at most `maxScreenFraction` of their display and
    /// contain `mouse`: a focused element that is the size of the window, or that does not contain the pointer
    /// that just made the selection, is not where the selection is.
    public static func isPlausibleSelection(_ bounds: Bounds, screens: [CGRect], mouse: CGPoint, fromFrame: Bool) -> Bool {
        let rect = CGRect(x: bounds.x, y: bounds.y, width: bounds.w, height: bounds.h)
        guard let screen = screens.first(where: { $0.intersects(rect) }) else { return false }
        guard rect.width <= screen.width, rect.height <= screen.height else { return false }
        guard fromFrame else { return true }
        let area = rect.width * rect.height
        guard area <= screen.width * screen.height * maxScreenFraction else { return false }
        return rect.insetBy(dx: -4, dy: -4).contains(mouse)
    }

    /// Current display frames, primary first.
    @MainActor
    static func currentScreens() -> [CGRect] {
        NSScreen.screens.map(\.frame)
    }
}

/// The bounds chain: `AXSelectedTextRange` → `AXBoundsForRange` → `AXFrame` (or position+size) → mouse.
/// Each AX rect must pass `AXRect.flipAndValidate` and `AXRect.isPlausibleSelection`; otherwise the next
/// source is tried, ending at the pointer location.
@MainActor
enum BoundsChain {
    struct Outcome: Sendable, Equatable {
        var bounds: Bounds
        var source: BoundsSource
    }

    static func locate(
        focused: AXUIElement?, screens: [CGRect] = AXRect.currentScreens(), mouse: CGPoint = NSEvent.mouseLocation
    ) -> Outcome {
        if let focused {
            if let range = AX.range(focused, kAXSelectedTextRangeAttribute), range.length > 0,
               let raw = AX.rect(focused, kAXBoundsForRangeParameterizedAttribute, forRange: range),
               let bounds = AXRect.flipAndValidate(raw, screens: screens),
               AXRect.isPlausibleSelection(bounds, screens: screens, mouse: mouse, fromFrame: false)
            {
                return Outcome(bounds: bounds, source: .range)
            }
            if let raw = frame(of: focused), let bounds = AXRect.flipAndValidate(raw, screens: screens),
               AXRect.isPlausibleSelection(bounds, screens: screens, mouse: mouse, fromFrame: true)
            {
                return Outcome(bounds: bounds, source: .frame)
            }
        }
        return Outcome(bounds: Bounds(x: mouse.x, y: mouse.y, w: 0, h: 0), source: .mouse)
    }

    private static func frame(of element: AXUIElement) -> CGRect? {
        if let rect = AX.rect(element, "AXFrame") { return rect }
        if let origin = AX.point(element, kAXPositionAttribute), let size = AX.size(element, kAXSizeAttribute) {
            return CGRect(origin: origin, size: size)
        }
        return nil
    }
}
