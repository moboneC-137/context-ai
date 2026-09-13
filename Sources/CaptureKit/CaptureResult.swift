import Foundation

/// Where the on-screen bounds of a capture came from, in order of preference.
public enum BoundsSource: String, Codable, Sendable {
    /// `AXSelectedTextRange` → `AXBoundsForRange` on the focused element.
    case range
    /// `AXFrame` (or `AXPosition` + `AXSize`) of the focused element.
    case frame
    /// `NSEvent.mouseLocation`; a zero-size rect at the pointer.
    case mouse
}

/// A rectangle in `NSScreen` coordinates (origin bottom-left of the primary screen).
public struct Bounds: Codable, Sendable, Equatable {
    public var x: Double
    public var y: Double
    public var w: Double
    public var h: Double

    public init(x: Double, y: Double, w: Double, h: Double) {
        self.x = x
        self.y = y
        self.w = w
        self.h = h
    }

    public init(_ rect: CGRect) {
        self.init(x: rect.origin.x, y: rect.origin.y, w: rect.width, h: rect.height)
    }
}

/// One tier's outcome inside a single capture.
public struct TierAttempt: Codable, Sendable, Equatable {
    public var tier: Int
    public var ok: Bool
    /// Elapsed wall time for this tier alone, including any retries.
    public var ms: Double
    public var error: String?
    /// Number of extra Tier 1 reads performed after a failure (Chromium/Electron only).
    public var retries: Int?
    /// Raw `AXError` behind a Tier 1 failure (e.g. −25212 `kAXErrorNoValue`, −25204 `kAXErrorCannotComplete`).
    public var axError: Int?
    /// `AXRole` of the focused element on a Tier 1 failure (e.g. `AXWebArea`, `AXTextArea`).
    public var role: String?

    public init(
        tier: Int, ok: Bool, ms: Double, error: String? = nil, retries: Int? = nil, axError: Int? = nil, role: String? = nil
    ) {
        self.tier = tier
        self.ok = ok
        self.ms = ms
        self.error = error
        self.retries = retries
        self.axError = axError
        self.role = role
    }
}

/// One JSON line of the probe's output.
public struct CaptureResult: Codable, Sendable, Equatable {
    public var ts: Date
    /// Bundle identifier of the frontmost app, or its name / pid when it has none.
    public var app: String
    /// Winning tier, or `nil` when no tier produced text.
    public var tier: Int?
    public var text: String?
    /// Length of the untruncated text.
    public var textLength: Int?
    public var bounds: Bounds?
    public var boundsSource: BoundsSource?
    /// Elapsed wall time of the bounds chain, when it ran.
    public var boundsMs: Double?
    /// `IsSecureEventInputEnabled()` at capture time.
    public var secureInput: Bool
    public var attempts: [TierAttempt]
    public var totalMs: Double
    /// `"no-selection"`, `"exhausted"`, or a chain-level failure such as `"no-frontmost-app"`.
    public var error: String?

    public init(
        ts: Date = Date(),
        app: String,
        tier: Int? = nil,
        text: String? = nil,
        textLength: Int? = nil,
        bounds: Bounds? = nil,
        boundsSource: BoundsSource? = nil,
        boundsMs: Double? = nil,
        secureInput: Bool = false,
        attempts: [TierAttempt] = [],
        totalMs: Double = 0,
        error: String? = nil
    ) {
        self.ts = ts
        self.app = app
        self.tier = tier
        self.text = text
        self.textLength = textLength
        self.bounds = bounds
        self.boundsSource = boundsSource
        self.boundsMs = boundsMs
        self.secureInput = secureInput
        self.attempts = attempts
        self.totalMs = totalMs
        self.error = error
    }

    /// `true` when some tier produced text.
    public var isHit: Bool { text != nil }

    /// `tier` is always present (`null` on a miss) so failure lines are greppable; other optionals are omitted.
    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(ts, forKey: .ts)
        try container.encode(app, forKey: .app)
        try container.encode(tier, forKey: .tier)
        try container.encodeIfPresent(text, forKey: .text)
        try container.encodeIfPresent(textLength, forKey: .textLength)
        try container.encodeIfPresent(bounds, forKey: .bounds)
        try container.encodeIfPresent(boundsSource, forKey: .boundsSource)
        try container.encodeIfPresent(boundsMs, forKey: .boundsMs)
        try container.encode(secureInput, forKey: .secureInput)
        try container.encode(attempts, forKey: .attempts)
        try container.encode(totalMs, forKey: .totalMs)
        try container.encodeIfPresent(error, forKey: .error)
    }

    /// A copy whose `text` is cut to `maxCharacters` (with a trailing ellipsis); `textLength` keeps the full count.
    public func truncatingText(to maxCharacters: Int) -> CaptureResult {
        guard maxCharacters > 0, let text, text.count > maxCharacters else { return self }
        var copy = self
        copy.text = String(text.prefix(maxCharacters)) + "…"
        return copy
    }

    /// Serialises to a single JSON line (no trailing newline). Keys are sorted so lines diff cleanly.
    public func jsonLine() -> String {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        // Encoding a Codable value with only JSON-representable fields cannot fail.
        let data = (try? encoder.encode(self)) ?? Data("{}".utf8)
        return String(decoding: data, as: UTF8.self)
    }
}
