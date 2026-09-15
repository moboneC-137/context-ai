import Foundation
import Testing
@testable import capture_spike

@Suite("Arguments")
struct ArgumentsTests {
    private func parse(_ args: String...) -> Result<Arguments, UsageError> {
        Arguments.parse(args)
    }

    private func failureMessage(_ result: Result<Arguments, UsageError>) -> String? {
        if case .failure(let error) = result { return error.message }
        return nil
    }

    @Test("the contract's invocation parses")
    func success() throws {
        let parsed = try parse("--once", "--max-text", "0", "--tiers", "1,3", "--no-enhanced-ax").get()
        #expect(parsed.once == true)
        #expect(parsed.tiers == [1, 3])
        #expect(parsed.enhancedAX == false)
        #expect(parsed.maxText == 0)
        #expect(parsed.help == false)
        #expect(parsed.captureOptions.tiers == [1, 3])
        #expect(parsed.captureOptions.enhancedAX == false)
    }

    @Test("defaults")
    func defaults() throws {
        let parsed = try parse().get()
        #expect(parsed.tiers == [1, 2, 3])
        #expect(parsed.enhancedAX == true)
        #expect(parsed.maxText == 200)
        #expect(parsed.once == false)
        #expect(parsed.tier1Retries == 0)
        #expect(parsed.captureOptions.tier1Retries == 0)
        #expect(try parse("--once").get().once == true)
        #expect(try parse("--help").get().help == true)
        #expect(try parse("-h").get().help == true)
    }

    @Test("--tier1-retries re-enables the Chromium retry loop for measurement")
    func tier1Retries() throws {
        let parsed = try parse("--tier1-retries", "3").get()
        #expect(parsed.tier1Retries == 3)
        #expect(parsed.captureOptions.tier1Retries == 3)
        #expect(failureMessage(parse("--tier1-retries", "-1")) == "--tier1-retries needs a non-negative integer")
        #expect(failureMessage(parse("--tier1-retries")) != nil)
    }

    @Test("bad values and unknown flags fail")
    func failures() {
        #expect(failureMessage(parse("--max-text", "-1")) == "--max-text needs a non-negative integer")
        #expect(failureMessage(parse("--max-text")) != nil)
        #expect(failureMessage(parse("--out", "matrix.jsonl")) == "unknown argument: --out")  // removed in step 4
        #expect(failureMessage(parse("--tiers", "4")) == "--tiers needs a comma-separated subset of 1,2,3")
        #expect(failureMessage(parse("--tiers")) != nil)
        #expect(failureMessage(parse("--bogus")) == "unknown argument: --bogus")
    }
}
