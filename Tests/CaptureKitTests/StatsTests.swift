import Foundation
import Testing
@testable import CaptureKit

@Suite("Stats")
struct StatsTests {
    @Test("nearest-rank percentiles")
    func percentiles() {
        let values: [Double] = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        #expect(Stats.percentile(values, 50) == 50)
        #expect(Stats.percentile(values, 95) == 100)
        #expect(Stats.percentile([7], 50) == 7)
        #expect(Stats.percentile([7], 95) == 7)
        #expect(Stats.percentile([], 50) == nil)
        #expect(Stats.percentile([3, 1, 2], 0) == 1)
    }

    @Test("per-app roll-up: attempts, hits, percentiles over hits, tier histogram")
    func summarize() {
        let rows = [
            CaptureResult(app: "com.apple.Safari", tier: 1, text: "a", totalMs: 5),
            CaptureResult(app: "com.apple.Safari", tier: 1, text: "b", totalMs: 9),
            CaptureResult(app: "com.apple.Safari", tier: 3, text: "c", totalMs: 120),
            CaptureResult(app: "com.apple.Safari", totalMs: 400, error: "exhausted"),
            CaptureResult(app: "com.apple.Terminal", totalMs: 2, error: "no-selection"),
        ]
        let stats = Stats.summarize(rows)
        #expect(stats.map(\.app) == ["com.apple.Safari", "com.apple.Terminal"])

        let safari = stats[0]
        #expect(safari.attempts == 4)
        #expect(safari.hits == 3)
        #expect(safari.hitRate == 0.75)
        #expect(safari.p50Ms == 9)
        #expect(safari.p95Ms == 120)
        #expect(safari.tierHistogram == [1: 2, 3: 1, nil: 1])

        let terminal = stats[1]
        #expect(terminal.hits == 0)
        #expect(terminal.p50Ms == nil)
        #expect(terminal.tierHistogram == [nil: 1])
    }

    @Test("empty session renders 'no captures'")
    func emptyTable() {
        #expect(Stats.renderTable([]) == "no captures")
    }

    @Test("table has one row per app with the expected columns")
    func table() {
        let stats = Stats.summarize([
            CaptureResult(app: "com.apple.Notes", tier: 2, text: "x", totalMs: 33.3),
            CaptureResult(app: "com.apple.Terminal", totalMs: 2.5, error: "no-selection"),
        ])
        let table = Stats.renderTable(stats)
        let lines = table.split(separator: "\n")
        #expect(lines.count == 4)
        #expect(lines[0].contains("hit-rate"))
        #expect(lines[2].contains("com.apple.Notes"))
        #expect(lines[2].contains("100%"))
        #expect(lines[2].contains("33.3"))
        #expect(lines[2].contains("0/1/0/0"))
        // A miss-only app shows 0% and "-" for both percentiles.
        #expect(lines[3].contains("com.apple.Terminal"))
        #expect(lines[3].contains("0%"))
        #expect(lines[3].contains("  -  "))
        #expect(lines[3].contains("0/0/0/1"))
    }
}
