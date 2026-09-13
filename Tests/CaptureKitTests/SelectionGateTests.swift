import Foundation
import Testing
@testable import CaptureKit

@Suite("SelectionGate")
struct SelectionGateTests {
    @Test("plain click is ignored")
    func plainClick() {
        var gate = SelectionGate()
        gate.mouseDown(at: CGPoint(x: 10, y: 10))
        #expect(gate.mouseUp(at: CGPoint(x: 11, y: 12), clickCount: 1, now: .now) == false)
    }

    @Test("drag beyond 3 pt counts")
    func drag() {
        var gate = SelectionGate()
        gate.mouseDown(at: CGPoint(x: 10, y: 10))
        #expect(gate.mouseUp(at: CGPoint(x: 14, y: 10), clickCount: 1, now: .now) == true)
    }

    @Test("drag of exactly 3 pt does not count")
    func dragAtThreshold() {
        var gate = SelectionGate()
        gate.mouseDown(at: CGPoint(x: 0, y: 0))
        #expect(gate.mouseUp(at: CGPoint(x: 3, y: 0), clickCount: 1, now: .now) == false)
    }

    @Test("double and triple click count without movement")
    func multiClick() {
        var gate = SelectionGate()
        gate.mouseDown(at: CGPoint(x: 5, y: 5))
        #expect(gate.mouseUp(at: CGPoint(x: 5, y: 5), clickCount: 2, now: .now) == true)
        gate.mouseDown(at: CGPoint(x: 5, y: 5))
        #expect(gate.mouseUp(at: CGPoint(x: 5, y: 5), clickCount: 3, now: .now + .milliseconds(500)) == true)
    }

    @Test("second gesture inside the 200 ms debounce is dropped, later one accepted")
    func debounce() {
        var gate = SelectionGate()
        let start = ContinuousClock.now
        gate.mouseDown(at: .zero)
        #expect(gate.mouseUp(at: CGPoint(x: 20, y: 0), clickCount: 1, now: start) == true)
        gate.mouseDown(at: .zero)
        #expect(gate.mouseUp(at: CGPoint(x: 20, y: 0), clickCount: 1, now: start + .milliseconds(100)) == false)
        gate.mouseDown(at: .zero)
        #expect(gate.mouseUp(at: CGPoint(x: 20, y: 0), clickCount: 1, now: start + .milliseconds(250)) == true)
    }

    @Test("mouseUp without a matching mouseDown is treated as zero travel")
    func upWithoutDown() {
        var gate = SelectionGate()
        #expect(gate.mouseUp(at: CGPoint(x: 100, y: 100), clickCount: 1, now: .now) == false)
        #expect(gate.mouseUp(at: CGPoint(x: 100, y: 100), clickCount: 2, now: .now) == true)
    }
}
