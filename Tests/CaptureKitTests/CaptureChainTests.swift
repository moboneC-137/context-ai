import AppKit
import Testing
@testable import CaptureKit

/// Scripts every hook so the chain's policy (order, gate, retries, bounds, errors) runs without any live app.
@MainActor
final class ScriptedHooks {
    var tier1Outcomes: [TierOutcome]
    var copyEnabled: Bool? = true
    var copyItemPresent = true
    var tier2Outcome: TierOutcome = .failure("timeout")
    var tier3Outcome: TierOutcome = .failure("timeout")
    var chromium = false
    var setEnhancedAXSucceeds = true

    private(set) var tier1Calls = 0
    private(set) var tier2Calls = 0
    private(set) var tier3Calls = 0
    private(set) var locateCalls = 0
    private(set) var enhancedAXLog: [(pid_t, Bool)] = []

    // Any AXUIElement works as an opaque handle; nothing here messages it.
    let element = AXUIElementCreateApplication(getpid())

    init(tier1: [TierOutcome]) {
        tier1Outcomes = tier1
    }

    var hooks: CaptureChainHooks {
        CaptureChainHooks(
            secureInput: { false },
            frontmostApp: { TargetApp(name: "test.app", pid: 4242) },
            isChromium: { _ in self.chromium },
            setEnhancedAX: { pid, on in
                self.enhancedAXLog.append((pid, on))
                return self.setEnhancedAXSucceeds
            },
            focusedElement: { _ in self.element },
            tier1: { _ in
                self.tier1Calls += 1
                return self.tier1Outcomes.count > 1 ? self.tier1Outcomes.removeFirst() : self.tier1Outcomes[0]
            },
            findCopyItem: { _ in self.copyItemPresent ? self.element : nil },
            copyItemEnabled: { _ in self.copyEnabled },
            tier2: { _, _ in self.tier2Calls += 1; return self.tier2Outcome },
            tier3: { _ in self.tier3Calls += 1; return self.tier3Outcome },
            locate: { _ in
                self.locateCalls += 1
                return BoundsChain.Outcome(bounds: Bounds(x: 1, y: 2, w: 3, h: 4), source: .range)
            }
        )
    }
}

@Suite("CaptureChain policy")
@MainActor
struct CaptureChainTests {
    private let fast = CaptureOptions(tier1Retries: 3, tier1RetryDelay: .milliseconds(1), clipboardTimeout: .milliseconds(1))

    @Test("Tier 1 hit stops the chain and sets bounds")
    func tier1Hit() async {
        let script = ScriptedHooks(tier1: [.success("hello")])
        let result = await CaptureChain(hooks: script.hooks).capture(options: fast)
        #expect(result.tier == 1)
        #expect(result.text == "hello")
        #expect(result.textLength == 5)
        #expect(result.attempts.map(\.tier) == [1])
        #expect(result.attempts[0].ok == true)
        #expect(result.attempts[0].retries == nil)
        #expect(result.error == nil)
        #expect(result.bounds == Bounds(x: 1, y: 2, w: 3, h: 4))
        #expect(result.boundsSource == .range)
        #expect(script.tier2Calls == 0 && script.tier3Calls == 0)
    }

    @Test("Tiers run 1 → 2 → 3 and stop at the first success")
    func fallThroughToTier3() async {
        let script = ScriptedHooks(tier1: [.failure("empty")])
        script.tier3Outcome = .success("from ⌘C")
        let result = await CaptureChain(hooks: script.hooks).capture(options: fast)
        #expect(result.attempts.map(\.tier) == [1, 2, 3])
        #expect(result.attempts.map(\.ok) == [false, false, true])
        #expect(result.attempts[1].error == "timeout")
        #expect(result.tier == 3)
        #expect(result.text == "from ⌘C")
        #expect(script.tier2Calls == 1 && script.tier3Calls == 1)
    }

    @Test("disabled Copy item short-circuits with no-selection and never touches Tier 2 or 3")
    func copyGate() async {
        let script = ScriptedHooks(tier1: [.failure("empty")])
        script.copyEnabled = false
        let result = await CaptureChain(hooks: script.hooks).capture(options: fast)
        #expect(result.error == "no-selection")
        #expect(result.tier == nil)
        #expect(result.attempts.map(\.tier) == [1, 2])
        #expect(result.attempts[1].error == "no-selection")
        #expect(script.tier2Calls == 0 && script.tier3Calls == 0)
        #expect(result.bounds == nil)
        #expect(script.locateCalls == 0)
    }

    @Test("gate also protects Tier 3 when Tier 2 is not enabled")
    func gateWithTiers13() async {
        let script = ScriptedHooks(tier1: [.failure("empty")])
        script.copyEnabled = false
        var options = fast
        options.tiers = [1, 3]
        let result = await CaptureChain(hooks: script.hooks).capture(options: options)
        #expect(result.error == "no-selection")
        #expect(result.attempts.map(\.tier) == [1, 3])
        #expect(script.tier3Calls == 0)
    }

    @Test("missing Copy menu item: Tier 2 reports no-copy-menu-item and Tier 3 still runs")
    func noCopyItem() async {
        let script = ScriptedHooks(tier1: [.failure("empty")])
        script.copyItemPresent = false
        let result = await CaptureChain(hooks: script.hooks).capture(options: fast)
        #expect(result.attempts[1].error == "no-copy-menu-item")
        #expect(script.tier2Calls == 0 && script.tier3Calls == 1)
        #expect(result.error == "exhausted")
    }

    @Test("every enabled tier failing yields exhausted with nil tier and bounds")
    func exhausted() async {
        let script = ScriptedHooks(tier1: [.failure("no-focused-element")])
        let result = await CaptureChain(hooks: script.hooks).capture(options: fast)
        #expect(result.error == "exhausted")
        #expect(result.tier == nil)
        #expect(result.text == nil)
        #expect(result.bounds == nil)
        #expect(result.boundsSource == nil)
        #expect(result.attempts.map(\.tier) == [1, 2, 3])
    }

    @Test("--tiers 1: Tier 1 failure is exhausted without any clipboard tier")
    func tier1Only() async {
        let script = ScriptedHooks(tier1: [.failure("empty")])
        script.tier2Outcome = .success("should not be reached")
        var options = fast
        options.tiers = [1]
        let result = await CaptureChain(hooks: script.hooks).capture(options: options)
        #expect(result.error == "exhausted")
        #expect(result.attempts.map(\.tier) == [1])
        #expect(script.tier2Calls == 0 && script.tier3Calls == 0)
    }

    @Test("Chromium: Tier 1 retries up to the limit, reports retries only when > 0")
    func chromiumRetries() async {
        let script = ScriptedHooks(tier1: [.failure("empty"), .failure("empty"), .success("late")])
        script.chromium = true
        let result = await CaptureChain(hooks: script.hooks).capture(options: fast)
        #expect(result.tier == 1)
        #expect(result.attempts[0].retries == 2)
        #expect(script.tier1Calls == 3)

        let exhaustedScript = ScriptedHooks(tier1: [.failure("empty")])
        exhaustedScript.chromium = true
        let miss = await CaptureChain(hooks: exhaustedScript.hooks).capture(options: fast)
        #expect(miss.attempts[0].retries == 3)
        #expect(exhaustedScript.tier1Calls == 4)
    }

    @Test("non-Chromium apps never retry Tier 1; --no-enhanced-ax disables it for Chromium too")
    func noRetries() async {
        let native = ScriptedHooks(tier1: [.failure("empty")])
        _ = await CaptureChain(hooks: native.hooks).capture(options: fast)
        #expect(native.tier1Calls == 1)
        #expect(native.enhancedAXLog.isEmpty)

        let chromiumOff = ScriptedHooks(tier1: [.failure("empty")])
        chromiumOff.chromium = true
        var options = fast
        options.enhancedAX = false
        _ = await CaptureChain(hooks: chromiumOff.hooks).capture(options: options)
        #expect(chromiumOff.tier1Calls == 1)
        #expect(chromiumOff.enhancedAXLog.isEmpty)
    }

    @Test("AXEnhancedUserInterface is set once per pid, only recorded on success, and reset on demand")
    func enhancedAXLifecycle() async {
        let script = ScriptedHooks(tier1: [.success("x")])
        script.chromium = true
        let chain = CaptureChain(hooks: script.hooks)
        _ = await chain.capture(options: fast)
        _ = await chain.capture(options: fast)
        #expect(script.enhancedAXLog.map(\.1) == [true])
        chain.resetEnhancedAX()
        #expect(script.enhancedAXLog.map(\.1) == [true, false])
        #expect(script.enhancedAXLog.map(\.0) == [4242, 4242])

        let failing = ScriptedHooks(tier1: [.success("x")])
        failing.chromium = true
        failing.setEnhancedAXSucceeds = false
        let failingChain = CaptureChain(hooks: failing.hooks)
        _ = await failingChain.capture(options: fast)
        _ = await failingChain.capture(options: fast)
        failingChain.resetEnhancedAX()
        // Not recorded on failure: retried on the next capture, and nothing to reset.
        #expect(failing.enhancedAXLog.map(\.1) == [true, true])
    }
}
