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

    /// Current display frames, primary first.
    @MainActor
    static func currentScreens() -> [CGRect] {
        NSScreen.screens.map(\.frame)
    }
}

/// The bounds chain: `AXSelectedTextRange` → `AXBoundsForRange` → `AXFrame` (or position+size) → mouse.
@MainActor
enum BoundsChain {
    struct Outcome: Sendable, Equatable {
        var bounds: Bounds
        var source: BoundsSource
    }

    static func locate(focused: AXUIElement?, screens: [CGRect] = AXRect.currentScreens()) -> Outcome {
        if let focused {
            if let range = AX.range(focused, kAXSelectedTextRangeAttribute), range.length > 0,
               let raw = AX.rect(focused, kAXBoundsForRangeParameterizedAttribute, forRange: range),
               let bounds = AXRect.flipAndValidate(raw, screens: screens)
            {
                return Outcome(bounds: bounds, source: .range)
            }
            if let raw = frame(of: focused), let bounds = AXRect.flipAndValidate(raw, screens: screens) {
                return Outcome(bounds: bounds, source: .frame)
            }
        }
        let mouse = NSEvent.mouseLocation
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
