import AppKit

/// A full copy of every item and every type on a pasteboard, taken before the clipboard tiers touch it
/// and written back afterwards so rich content (RTF, images, file URLs) survives a capture.
public struct PasteboardSnapshot: Sendable, Equatable {
    /// One pasteboard item: its types in declared order, and the bytes for each type that could be read.
    public struct Item: Sendable, Equatable {
        public var types: [String]
        public var data: [String: Data]

        public init(types: [String], data: [String: Data]) {
            self.types = types
            self.data = data
        }
    }

    public var items: [Item]
    public var changeCount: Int

    public init(items: [Item], changeCount: Int) {
        self.items = items
        self.changeCount = changeCount
    }

    /// Captures all items and types currently on `pasteboard`.
    @MainActor
    public static func take(from pasteboard: NSPasteboard = .general) -> PasteboardSnapshot {
        let items = (pasteboard.pasteboardItems ?? []).map { item -> Item in
            var data: [String: Data] = [:]
            for type in item.types {
                if let bytes = item.data(forType: type) {
                    data[type.rawValue] = bytes
                }
            }
            return Item(types: item.types.map(\.rawValue), data: data)
        }
        return PasteboardSnapshot(items: items, changeCount: pasteboard.changeCount)
    }

    /// Replaces the contents of `pasteboard` with the snapshot. Types whose bytes could not be read
    /// when the snapshot was taken (promised or private data) are dropped; everything else round-trips.
    @MainActor
    public func restore(to pasteboard: NSPasteboard = .general) {
        pasteboard.clearContents()
        let restored = items.compactMap { item -> NSPasteboardItem? in
            let pasteboardItem = NSPasteboardItem()
            var wroteAny = false
            for type in item.types {
                guard let bytes = item.data[type] else { continue }
                if pasteboardItem.setData(bytes, forType: NSPasteboard.PasteboardType(type)) {
                    wroteAny = true
                }
            }
            return wroteAny ? pasteboardItem : nil
        }
        if !restored.isEmpty {
            pasteboard.writeObjects(restored)
        }
    }

    /// Restores only if `pasteboard.changeCount` has moved since the snapshot; an untouched pasteboard is
    /// left exactly as it is (no changeCount bump, no dropped unreadable types). Returns whether it restored.
    @MainActor
    @discardableResult
    public func restoreIfChanged(to pasteboard: NSPasteboard = .general) -> Bool {
        guard pasteboard.changeCount != changeCount else { return false }
        restore(to: pasteboard)
        return true
    }

    /// The item types in order, one array per item — handy for asserting a round-trip.
    public var typeLists: [[String]] { items.map(\.types) }
}

public enum PasteboardWait {
    /// Polls `pasteboard.changeCount` until it moves past `baseline` or `timeout` elapses.
    /// Returns `true` only when the count actually advanced — a stale string is never treated as success.
    @MainActor
    public static func forChange(
        on pasteboard: NSPasteboard = .general,
        from baseline: Int,
        timeout: Duration = .milliseconds(150),
        pollInterval: Duration = .milliseconds(5)
    ) async -> Bool {
        let clock = ContinuousClock()
        let deadline = clock.now + timeout
        while true {
            if pasteboard.changeCount != baseline { return true }
            if clock.now >= deadline { return false }
            try? await Task.sleep(for: pollInterval)
        }
    }
}
