import Foundation
import Testing
@testable import CaptureKit

@Suite("AXRect validation")
struct BoundsTests {
    /// A 1440×900 primary display plus a 1920×1080 display to its right.
    let screens = [
        CGRect(x: 0, y: 0, width: 1440, height: 900),
        CGRect(x: 1440, y: -180, width: 1920, height: 1080),
    ]

    @Test("AX top-left rect is flipped to NSScreen bottom-left")
    func flip() {
        let ax = CGRect(x: 312, y: 337.5, width: 280, height: 18)
        let bounds = AXRect.flipAndValidate(ax, screens: screens)
        #expect(bounds == Bounds(x: 312, y: 900 - 337.5 - 18, w: 280, h: 18))
    }

    @Test("zero-size rects are rejected")
    func zeroRect() {
        #expect(AXRect.flipAndValidate(.zero, screens: screens) == nil)
        #expect(AXRect.flipAndValidate(CGRect(x: 10, y: 10, width: 0, height: 18), screens: screens) == nil)
        #expect(AXRect.flipAndValidate(CGRect(x: 10, y: 10, width: 18, height: 0), screens: screens) == nil)
    }

    @Test("y ≈ -9800 garbage from web apps is rejected")
    func offscreenGarbage() {
        let garbage = CGRect(x: 100, y: -9800, width: 50, height: 18)
        #expect(AXRect.flipAndValidate(garbage, screens: screens) == nil)
    }

    @Test("non-finite rects are rejected")
    func nonFinite() {
        #expect(AXRect.flipAndValidate(CGRect(x: CGFloat.nan, y: 0, width: 10, height: 10), screens: screens) == nil)
        #expect(AXRect.flipAndValidate(CGRect(x: 0, y: 0, width: CGFloat.infinity, height: 10), screens: screens) == nil)
    }

    @Test("a rect on the secondary display is accepted")
    func secondaryDisplay() {
        // In AX coordinates the secondary display spans y = -180...900 → flipped range 0...1080.
        let ax = CGRect(x: 2000, y: 100, width: 40, height: 20)
        let bounds = AXRect.flipAndValidate(ax, screens: screens)
        #expect(bounds == Bounds(x: 2000, y: 900 - 120, w: 40, h: 20))
    }

    @Test("bounds chain falls back to the mouse location with a zero-size rect")
    @MainActor
    func mouseFallback() {
        let outcome = BoundsChain.locate(focused: nil, screens: screens)
        #expect(outcome.source == .mouse)
        #expect(outcome.bounds.w == 0 && outcome.bounds.h == 0)
    }

    @Test("no screens means no bounds")
    func noScreens() {
        #expect(AXRect.flipAndValidate(CGRect(x: 1, y: 1, width: 1, height: 1), screens: []) == nil)
    }
}
