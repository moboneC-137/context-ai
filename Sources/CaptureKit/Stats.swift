import Foundation

/// Per-app roll-up of a session's captures.
public struct AppStats: Sendable, Equatable {
    public var app: String
    public var attempts: Int
    public var hits: Int
    /// p50 / p95 of `totalMs` over hits only; `nil` when the app never hit.
    public var p50Ms: Double?
    public var p95Ms: Double?
    /// Winning tier → count. Failures are counted under `nil`.
    public var tierHistogram: [Int?: Int]

    public var hitRate: Double { attempts == 0 ? 0 : Double(hits) / Double(attempts) }

    public init(app: String, attempts: Int, hits: Int, p50Ms: Double?, p95Ms: Double?, tierHistogram: [Int?: Int]) {
        self.app = app
        self.attempts = attempts
        self.hits = hits
        self.p50Ms = p50Ms
        self.p95Ms = p95Ms
        self.tierHistogram = tierHistogram
    }
}

public enum Stats {
    /// Nearest-rank percentile: the smallest value such that at least `p`% of samples are ≤ it.
    public static func percentile(_ values: [Double], _ p: Double) -> Double? {
        guard !values.isEmpty else { return nil }
        let sorted = values.sorted()
        let rank = Int((p / 100 * Double(sorted.count)).rounded(.up))
        let index = min(max(rank - 1, 0), sorted.count - 1)
        return sorted[index]
    }

    /// Groups results by app (sorted by app name) and computes hit-rate, latency percentiles and tier histogram.
    public static func summarize(_ results: [CaptureResult]) -> [AppStats] {
        let grouped = Dictionary(grouping: results, by: \.app)
        return grouped.keys.sorted().map { app in
            let rows = grouped[app] ?? []
            let hitLatencies = rows.filter(\.isHit).map(\.totalMs)
            var histogram: [Int?: Int] = [:]
            for row in rows { histogram[row.tier, default: 0] += 1 }
            return AppStats(
                app: app,
                attempts: rows.count,
                hits: hitLatencies.count,
                p50Ms: percentile(hitLatencies, 50),
                p95Ms: percentile(hitLatencies, 95),
                tierHistogram: histogram
            )
        }
    }

    /// A fixed-width text table for the terminal; `"no captures"` when the session is empty.
    public static func renderTable(_ stats: [AppStats]) -> String {
        guard !stats.isEmpty else { return "no captures" }
        let header = ["app", "attempts", "hits", "hit-rate", "p50 ms", "p95 ms", "tiers (1/2/3/fail)"]
        let rows = stats.map { s -> [String] in
            [
                s.app,
                String(s.attempts),
                String(s.hits),
                String(format: "%.0f%%", s.hitRate * 100),
                s.p50Ms.map { String(format: "%.1f", $0) } ?? "-",
                s.p95Ms.map { String(format: "%.1f", $0) } ?? "-",
                [1, 2, 3].map { String(s.tierHistogram[$0] ?? 0) }.joined(separator: "/") + "/" + String(s.tierHistogram[nil] ?? 0),
            ]
        }
        let all = [header] + rows
        let widths = (0..<header.count).map { column in all.map { $0[column].count }.max() ?? 0 }
        func line(_ cells: [String]) -> String {
            zip(cells, widths).map { $0.padding(toLength: $1, withPad: " ", startingAt: 0) }.joined(separator: "  ")
        }
        let separator = widths.map { String(repeating: "-", count: $0) }.joined(separator: "  ")
        return ([line(header), separator] + rows.map(line)).joined(separator: "\n")
    }
}
