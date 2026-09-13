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

    @Test("documented combination parses")
    func success() throws {
        let parsed = try parse("--out", "matrix.jsonl", "--tiers", "1,3", "--no-enhanced-ax", "--max-text", "0").get()
        #expect(parsed.outPath == "matrix.jsonl")
        #expect(parsed.tiers == [1, 3])
        #expect(parsed.enhancedAX == false)
        #expect(parsed.maxText == 0)
        #expect(parsed.once == false)
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
        #expect(parsed.outPath == nil)
        #expect(try parse("--once").get().once == true)
        #expect(try parse("--help").get().help == true)
        #expect(try parse("-h").get().help == true)
    }

    @Test("bad values and unknown flags fail")
    func failures() {
        #expect(failureMessage(parse("--max-text", "-1")) == "--max-text needs a non-negative integer")
        #expect(failureMessage(parse("--max-text")) != nil)
        #expect(failureMessage(parse("--out")) == "--out needs a file path")
        #expect(failureMessage(parse("--tiers", "4")) == "--tiers needs a comma-separated subset of 1,2,3")
        #expect(failureMessage(parse("--tiers")) != nil)
        #expect(failureMessage(parse("--bogus")) == "unknown argument: --bogus")
    }
}
