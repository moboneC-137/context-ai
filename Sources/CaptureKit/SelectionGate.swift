import Foundation

/// Decides which `mouseUp` events count as "the user just selected something".
/// Pure value type: feed it `mouseDown`/`mouseUp` with points and a clock instant, get a yes/no.
public struct SelectionGate: Sendable, Equatable {
    /// Minimum pointer travel between mouseDown and mouseUp for a single click to count as a drag-select.
    public var dragThreshold: Double
    /// Minimum spacing between two accepted gestures.
    public var debounce: Duration

    private var downPoint: CGPoint?
    private var lastAccepted: ContinuousClock.Instant?

    public init(dragThreshold: Double = 3, debounce: Duration = .milliseconds(200)) {
        self.dragThreshold = dragThreshold
        self.debounce = debounce
    }

    public mutating func mouseDown(at point: CGPoint) {
        downPoint = point
    }

    /// Returns `true` when this mouseUp should trigger a capture: the pointer moved more than
    /// `dragThreshold` points since mouseDown, or `clickCount` is at least 2 — and the debounce has elapsed.
    public mutating func mouseUp(at point: CGPoint, clickCount: Int, now: ContinuousClock.Instant) -> Bool {
        defer { downPoint = nil }
        let distance = downPoint.map { hypot(point.x - $0.x, point.y - $0.y) } ?? 0
        let qualifies = distance > dragThreshold || clickCount >= 2
        guard qualifies else { return false }
        if let last = lastAccepted, now - last < debounce { return false }
        lastAccepted = now
        return true
    }
}
