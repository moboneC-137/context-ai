import Foundation
import Testing
@testable import CaptureKit

@Suite("CaptureResult")
struct CaptureResultTests {
    @Test("JSON line is a single line with sorted keys and ISO-8601 timestamp")
    func jsonLine() throws {
        let result = CaptureResult(
            ts: Date(timeIntervalSince1970: 0),
            app: "com.apple.Safari",
            tier: 1,
            text: "hello",
            textLength: 5,
            bounds: Bounds(x: 1, y: 2, w: 3, h: 4),
            boundsSource: .range,
            secureInput: false,
            attempts: [TierAttempt(tier: 1, ok: true, ms: 4.1)],
            totalMs: 6.8
        )
        let line = result.jsonLine()
        #expect(!line.contains("\n"))
        #expect(line.contains("\"ts\":\"1970-01-01T00:00:00Z\""))
        #expect(line.contains("\"boundsSource\":\"range\""))
        #expect(line.contains("\"attempts\":[{\"ms\":4.1,\"ok\":true,\"tier\":1}]"))
        #expect(!line.contains("error"))

        let decoded = try JSONDecoder.iso.decode(CaptureResult.self, from: Data(line.utf8))
        #expect(decoded == result)
    }

    @Test("failure line carries tier null and error")
    func failureLine() {
        let line = CaptureResult(app: "com.apple.Terminal", totalMs: 1, error: "no-selection").jsonLine()
        #expect(line.contains("\"error\":\"no-selection\""))
        #expect(line.contains("\"tier\":null"))
        #expect(!line.contains("\"text\""))
        #expect(!line.contains("\"bounds\""))
    }

    @Test("truncation keeps textLength and appends an ellipsis")
    func truncation() {
        let long = String(repeating: "x", count: 412)
        let result = CaptureResult(app: "a", tier: 1, text: long, textLength: 412).truncatingText(to: 10)
        #expect(result.text == String(repeating: "x", count: 10) + "…")
        #expect(result.textLength == 412)
        #expect(CaptureResult(app: "a", tier: 1, text: "short").truncatingText(to: 0).text == "short")
    }

    @Test("--tiers parsing")
    func tiers() {
        #expect(CaptureOptions.parseTiers("1,2,3") == [1, 2, 3])
        #expect(CaptureOptions.parseTiers("3, 1") == [1, 3])
        #expect(CaptureOptions.parseTiers("1") == [1])
        #expect(CaptureOptions.parseTiers("") == nil)
        #expect(CaptureOptions.parseTiers("4") == nil)
        #expect(CaptureOptions.parseTiers("1,,2") == nil)
        #expect(CaptureOptions.parseTiers("a") == nil)
    }
}

private extension JSONDecoder {
    static var iso: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}
