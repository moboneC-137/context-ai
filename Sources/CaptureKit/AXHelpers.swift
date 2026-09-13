import AppKit
import ApplicationServices

/// Thin typed wrappers over the C accessibility API. Every call goes through the main actor because
/// `AXUIElement` values are not `Sendable` and the AX client API is not thread-safe for our purposes.
@MainActor
enum AX {
    /// The UI element for a running application.
    /// Messaging to it times out after 0.5 s so a hung target cannot block the main actor for the 6 s default.
    static func application(pid: pid_t) -> AXUIElement {
        let element = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(element, 0.5)
        return element
    }

    /// Reads an attribute and casts it to `T`, or returns `nil` when the attribute is missing or of another type.
    static func attribute<T>(_ element: AXUIElement, _ name: String, as type: T.Type = T.self) -> T? {
        var value: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(element, name as CFString, &value)
        guard status == .success, let value else { return nil }
        return value as? T
    }

    /// Reads an `AXUIElement`-typed attribute.
    static func element(_ element: AXUIElement, _ name: String) -> AXUIElement? {
        var value: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(element, name as CFString, &value)
        guard status == .success, let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        // Safe: the type ID was checked above.
        return unsafeDowncast(value, to: AXUIElement.self)
    }

    /// Reads an array-of-elements attribute (e.g. `AXChildren`).
    static func elements(_ element: AXUIElement, _ name: String) -> [AXUIElement] {
        var value: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(element, name as CFString, &value)
        guard status == .success, let value, let array = value as? [AnyObject] else { return [] }
        return array.compactMap { item in
            CFGetTypeID(item) == AXUIElementGetTypeID() ? unsafeDowncast(item, to: AXUIElement.self) : nil
        }
    }

    static func string(_ element: AXUIElement, _ name: String) -> String? {
        attribute(element, name, as: String.self)
    }

    static func bool(_ element: AXUIElement, _ name: String) -> Bool? {
        guard let value: AnyObject = attribute(element, name) else { return nil }
        if let number = value as? NSNumber { return number.boolValue }
        return nil
    }

    static func int(_ element: AXUIElement, _ name: String) -> Int? {
        guard let value: AnyObject = attribute(element, name) else { return nil }
        if let number = value as? NSNumber { return number.intValue }
        return nil
    }

    static func range(_ element: AXUIElement, _ name: String) -> CFRange? {
        guard let value: AnyObject = attribute(element, name) else { return nil }
        return cfRange(from: value)
    }

    static func rect(_ element: AXUIElement, _ name: String) -> CGRect? {
        guard let value: AnyObject = attribute(element, name) else { return nil }
        return cgRect(from: value)
    }

    static func point(_ element: AXUIElement, _ name: String) -> CGPoint? {
        guard let value: AnyObject = attribute(element, name) else { return nil }
        return cgPoint(from: value)
    }

    static func size(_ element: AXUIElement, _ name: String) -> CGSize? {
        guard let value: AnyObject = attribute(element, name) else { return nil }
        return cgSize(from: value)
    }

    /// Reads a parameterized attribute (e.g. `AXBoundsForRange`) whose parameter is a `CFRange`.
    static func rect(_ element: AXUIElement, _ name: String, forRange range: CFRange) -> CGRect? {
        var range = range
        guard let parameter = AXValueCreate(.cfRange, &range) else { return nil }
        var value: CFTypeRef?
        let status = AXUIElementCopyParameterizedAttributeValue(element, name as CFString, parameter, &value)
        guard status == .success, let value else { return nil }
        return cgRect(from: value)
    }

    @discardableResult
    static func set(_ element: AXUIElement, _ name: String, to value: CFTypeRef) -> Bool {
        AXUIElementSetAttributeValue(element, name as CFString, value) == .success
    }

    @discardableResult
    static func perform(_ element: AXUIElement, action: String) -> Bool {
        AXUIElementPerformAction(element, action as CFString) == .success
    }

    // MARK: AXValue unpacking

    private static func cfRange(from value: AnyObject) -> CFRange? {
        guard CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
        let axValue = unsafeDowncast(value, to: AXValue.self)
        guard AXValueGetType(axValue) == .cfRange else { return nil }
        var range = CFRange()
        return AXValueGetValue(axValue, .cfRange, &range) ? range : nil
    }

    private static func cgRect(from value: AnyObject) -> CGRect? {
        guard CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
        let axValue = unsafeDowncast(value, to: AXValue.self)
        guard AXValueGetType(axValue) == .cgRect else { return nil }
        var rect = CGRect.zero
        return AXValueGetValue(axValue, .cgRect, &rect) ? rect : nil
    }

    private static func cgPoint(from value: AnyObject) -> CGPoint? {
        guard CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
        let axValue = unsafeDowncast(value, to: AXValue.self)
        guard AXValueGetType(axValue) == .cgPoint else { return nil }
        var point = CGPoint.zero
        return AXValueGetValue(axValue, .cgPoint, &point) ? point : nil
    }

    private static func cgSize(from value: AnyObject) -> CGSize? {
        guard CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
        let axValue = unsafeDowncast(value, to: AXValue.self)
        guard AXValueGetType(axValue) == .cgSize else { return nil }
        var size = CGSize.zero
        return AXValueGetValue(axValue, .cgSize, &size) ? size : nil
    }
}

/// Accessibility trust helpers used by the CLI.
public enum AccessibilityTrust {
    /// Whether this process may use the accessibility API.
    @MainActor
    public static func isTrusted() -> Bool {
        AXIsProcessTrusted()
    }

    /// Asks macOS to show the "grant Accessibility" prompt once. Returns the current trust state.
    @MainActor
    @discardableResult
    public static func promptIfNeeded() -> Bool {
        // The value of kAXTrustedCheckOptionPrompt is the literal "AXTrustedCheckOptionPrompt"; spelling it
        // out avoids touching a global C var that Swift 6 cannot prove concurrency-safe.
        return AXIsProcessTrustedWithOptions(["AXTrustedCheckOptionPrompt": true] as CFDictionary)
    }
}
