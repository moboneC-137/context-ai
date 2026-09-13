import AppKit
import ApplicationServices
import Carbon.HIToolbox

/// Knobs for one capture. Defaults match the spike's measurement protocol.
public struct CaptureOptions: Sendable, Equatable {
    /// Tiers to run; they always run in ascending order (1 → 2 → 3). Anything outside 1...3 is ignored.
    public var tiers: [Int]
    /// Set `AXEnhancedUserInterface` on Chromium/Electron apps and retry Tier 1 on any failure.
    public var enhancedAX: Bool
    /// How many extra Tier 1 reads to make on an empty result (Chromium/Electron only).
    public var tier1Retries: Int
    /// Pause between Tier 1 retries.
    public var tier1RetryDelay: Duration
    /// Longest wait for `changeCount` to advance in Tiers 2 and 3.
    public var clipboardTimeout: Duration

    public init(
        tiers: [Int] = [1, 2, 3],
        enhancedAX: Bool = true,
        tier1Retries: Int = 3,
        tier1RetryDelay: Duration = .milliseconds(150),
        clipboardTimeout: Duration = .milliseconds(150)
    ) {
        self.tiers = tiers
        self.enhancedAX = enhancedAX
        self.tier1Retries = tier1Retries
        self.tier1RetryDelay = tier1RetryDelay
        self.clipboardTimeout = clipboardTimeout
    }

    /// Parses `"1,2,3"`-style lists. Returns `nil` for anything that is not a comma-separated subset of 1...3.
    public static func parseTiers(_ text: String) -> [Int]? {
        var tiers: [Int] = []
        for part in text.split(separator: ",", omittingEmptySubsequences: false) {
            guard let tier = Int(part.trimmingCharacters(in: .whitespaces)), (1...3).contains(tier) else { return nil }
            if !tiers.contains(tier) { tiers.append(tier) }
        }
        return tiers.isEmpty ? nil : tiers.sorted()
    }
}

/// The frontmost application a capture targets.
struct TargetApp: Equatable, Sendable {
    var name: String
    var pid: pid_t
}

/// Seam between the chain's policy (order, gate, retries, timing) and the live AX / pasteboard operations,
/// so the policy can be unit-tested with scripted outcomes. Defaults to the real implementations.
struct CaptureChainHooks {
    var secureInput: @MainActor () -> Bool
    var frontmostApp: @MainActor () -> TargetApp?
    var isChromium: @MainActor (pid_t) -> Bool
    /// Sets `AXEnhancedUserInterface` on the app; returns whether the AX call succeeded.
    var setEnhancedAX: @MainActor (pid_t, Bool) -> Bool
    var focusedElement: @MainActor (pid_t) -> AXUIElement?
    var tier1: @MainActor (AXUIElement?) -> TierOutcome
    var findCopyItem: @MainActor (pid_t) -> AXUIElement?
    var copyItemEnabled: @MainActor (AXUIElement) -> Bool?
    var tier2: @MainActor (AXUIElement, Duration) async -> TierOutcome
    var tier3: @MainActor (Duration) async -> TierOutcome
    var locate: @MainActor (AXUIElement?) -> BoundsChain.Outcome

    @MainActor
    static func live(pasteboard: NSPasteboard) -> CaptureChainHooks {
        CaptureChainHooks(
            secureInput: { IsSecureEventInputEnabled() },
            frontmostApp: {
                guard let app = NSWorkspace.shared.frontmostApplication else { return nil }
                return TargetApp(
                    name: app.bundleIdentifier ?? app.localizedName ?? "pid:\(app.processIdentifier)",
                    pid: app.processIdentifier
                )
            },
            isChromium: { pid in
                NSRunningApplication(processIdentifier: pid).map(CaptureChain.isChromiumBased) ?? false
            },
            setEnhancedAX: { pid, on in
                AX.set(AX.application(pid: pid), "AXEnhancedUserInterface", to: on ? kCFBooleanTrue : kCFBooleanFalse)
            },
            focusedElement: { pid in AX.element(AX.application(pid: pid), kAXFocusedUIElementAttribute) },
            tier1: { Tiers.tier1(focused: $0) },
            findCopyItem: { pid in Tiers.findCopyMenuItem(app: AX.application(pid: pid)) },
            copyItemEnabled: { Tiers.copyItemEnabled($0) },
            tier2: { item, timeout in await Tiers.tier2(copyItem: item, pasteboard: pasteboard, timeout: timeout) },
            tier3: { timeout in await Tiers.tier3(pasteboard: pasteboard, timeout: timeout) },
            locate: { BoundsChain.locate(focused: $0) }
        )
    }
}

/// Runs the layered capture — Tier 1 AX, Tier 2 menu Copy, Tier 3 synthetic ⌘C — plus the bounds chain,
/// with per-tier timing. Stateful only for per-app caches (Copy menu item, Chromium detection, enhanced AX).
@MainActor
public final class CaptureChain {
    private let hooks: CaptureChainHooks
    private let clock = ContinuousClock()
    private var copyItemCache: [pid_t: AXUIElement] = [:]
    private var chromiumCache: [pid_t: Bool] = [:]
    private var enhancedAXSet: Set<pid_t> = []

    public convenience init(pasteboard: NSPasteboard = .general) {
        self.init(hooks: .live(pasteboard: pasteboard))
    }

    init(hooks: CaptureChainHooks) {
        self.hooks = hooks
    }

    /// Turns `AXEnhancedUserInterface` back off in every app this chain switched it on for.
    /// Call before exiting so Chromium/Electron apps do not keep paying for the full accessibility tree.
    public func resetEnhancedAX() {
        for pid in enhancedAXSet {
            _ = hooks.setEnhancedAX(pid, false)
        }
        enhancedAXSet.removeAll()
    }

    /// Captures the selection of the frontmost application.
    public func capture(options: CaptureOptions = CaptureOptions()) async -> CaptureResult {
        let start = clock.now
        let secure = hooks.secureInput()

        guard let app = hooks.frontmostApp() else {
            return CaptureResult(
                app: "unknown",
                secureInput: secure,
                totalMs: elapsedMs(since: start),
                error: "no-frontmost-app"
            )
        }
        let appName = app.name
        let chromium = options.enhancedAX && isChromium(app.pid)
        if chromium, !enhancedAXSet.contains(app.pid) {
            // Asks Chromium/Electron to build its full accessibility tree; once per process is enough.
            if hooks.setEnhancedAX(app.pid, true) {
                enhancedAXSet.insert(app.pid)
            }
        }

        // Resolved lazily so the (sometimes slow, first-contact) AX round-trip is charged to the tier that needs it.
        let focus = FocusedElementCache()
        func resolveFocused(refresh: Bool = false) -> AXUIElement? {
            if refresh || !focus.resolved {
                focus.element = hooks.focusedElement(app.pid) ?? (refresh ? focus.element : nil)
                focus.resolved = true
            }
            return focus.element
        }
        var attempts: [TierAttempt] = []
        var text: String?
        var winningTier: Int?
        var error: String?
        var gateChecked = false

        tierLoop: for tier in options.tiers where (1...3).contains(tier) {
            let tierStart = clock.now
            switch tier {
            case 1:
                var outcome = hooks.tier1(resolveFocused())
                var retries = 0
                while chromium, !outcome.ok, retries < options.tier1Retries {
                    retries += 1
                    try? await Task.sleep(for: options.tier1RetryDelay)
                    outcome = hooks.tier1(resolveFocused(refresh: true))
                }
                attempts.append(TierAttempt(
                    tier: 1, ok: outcome.ok, ms: elapsedMs(since: tierStart),
                    error: outcome.error, retries: retries > 0 ? retries : nil
                ))
                if let hit = outcome.text { text = hit; winningTier = 1; break tierLoop }

            case 2, 3:
                // Cheap "is anything selected?" gate, checked once before the first clipboard tier.
                let copyItem = copyMenuItem(for: app.pid)
                if !gateChecked {
                    gateChecked = true
                    if let copyItem, hooks.copyItemEnabled(copyItem) == false {
                        attempts.append(TierAttempt(tier: tier, ok: false, ms: elapsedMs(since: tierStart), error: "no-selection"))
                        error = "no-selection"
                        break tierLoop
                    }
                }
                let outcome: TierOutcome
                if tier == 2 {
                    if let copyItem {
                        outcome = await hooks.tier2(copyItem, options.clipboardTimeout)
                    } else {
                        outcome = .failure("no-copy-menu-item")
                    }
                } else {
                    outcome = await hooks.tier3(options.clipboardTimeout)
                }
                attempts.append(TierAttempt(tier: tier, ok: outcome.ok, ms: elapsedMs(since: tierStart), error: outcome.error))
                if let hit = outcome.text { text = hit; winningTier = tier; break tierLoop }

            default:
                continue
            }
        }

        var result = CaptureResult(
            app: appName,
            tier: winningTier,
            text: text,
            textLength: text?.count,
            secureInput: secure,
            attempts: attempts
        )
        if text != nil {
            let boundsStart = clock.now
            let located = hooks.locate(resolveFocused())
            result.bounds = located.bounds
            result.boundsSource = located.source
            result.boundsMs = elapsedMs(since: boundsStart)
        } else {
            result.error = error ?? "exhausted"
        }
        result.totalMs = elapsedMs(since: start)
        return result
    }

    // MARK: Helpers

    private func elapsedMs(since instant: ContinuousClock.Instant) -> Double {
        let duration = clock.now - instant
        let (seconds, attoseconds) = duration.components
        return (Double(seconds) * 1_000 + Double(attoseconds) / 1e15).rounded(toPlaces: 2)
    }

    /// The app's Copy menu item, cached per pid and re-resolved when the cached element has gone stale.
    private func copyMenuItem(for pid: pid_t) -> AXUIElement? {
        if let cached = copyItemCache[pid], hooks.copyItemEnabled(cached) != nil {
            return cached
        }
        copyItemCache[pid] = nil
        guard let found = hooks.findCopyItem(pid) else { return nil }
        copyItemCache[pid] = found
        return found
    }

    private func isChromium(_ pid: pid_t) -> Bool {
        if let cached = chromiumCache[pid] { return cached }
        let result = hooks.isChromium(pid)
        chromiumCache[pid] = result
        return result
    }

    /// Chromium and Electron apps build their accessibility tree lazily; they are recognised by bundle
    /// identifier or by the framework they embed.
    static func isChromiumBased(_ app: NSRunningApplication) -> Bool {
        let knownPrefixes = [
            "com.google.Chrome", "org.chromium", "com.brave.Browser", "com.microsoft.edgemac",
            "com.vivaldi.Vivaldi", "com.operasoftware", "company.thebrowser.Browser",
            "com.microsoft.VSCode", "com.tinyspeck.slackmacgap", "com.hnc.Discord",
        ]
        if let id = app.bundleIdentifier, knownPrefixes.contains(where: { id.hasPrefix($0) }) {
            return true
        }
        guard let bundle = app.bundleURL else { return false }
        let frameworks = bundle.appendingPathComponent("Contents/Frameworks")
        let names = (try? FileManager.default.contentsOfDirectory(atPath: frameworks.path)) ?? []
        return names.contains { name in
            name.localizedCaseInsensitiveContains("Electron Framework")
                || name.localizedCaseInsensitiveContains("Chromium")
                || name.localizedCaseInsensitiveContains("Chrome Framework")
        }
    }
}

/// Holds the focused element for one capture; a main-actor class rather than captured locals so the
/// non-`Sendable` `AXUIElement` stays in main-actor-isolated storage.
@MainActor
private final class FocusedElementCache {
    var element: AXUIElement?
    var resolved = false
}

extension Double {
    func rounded(toPlaces places: Int) -> Double {
        let factor = pow(10.0, Double(places))
        return (self * factor).rounded() / factor
    }
}
