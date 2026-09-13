import CaptureKit
import Foundation

// MARK: - Flags

struct UsageError: Error {
    let message: String
}

struct Arguments {
    var once = false
    var outPath: String?
    var tiers: [Int] = [1, 2, 3]
    var enhancedAX = true
    var tier1Retries = 0
    var maxText = 200
    var help = false

    static let usage = """
    usage: capture-spike [--once] [--out FILE] [--tiers 1,2,3] [--no-enhanced-ax] [--tier1-retries N] [--max-text N] [--help]

    Headless probe for cross-app selected-text capture. Listens for selection gestures (drag > 3 pt or
    double/triple-click) and, on each mouseUp, runs Tier 1 (AX kAXSelectedText) → Tier 2 (AX press on
    Edit > Copy) → Tier 3 (synthetic ⌘C), then the bounds chain. Prints one JSON line per gesture to
    stdout; Ctrl-C prints a per-app summary to stderr.

      --once             Run the chain once on the currently focused app, print one line, exit 0 (1 if no text).
      --out FILE         Also append every JSON line to FILE.
      --tiers 1,2,3      Which tiers to run; they always run in ascending order 1 → 2 → 3 (default: 1,2,3).
      --no-enhanced-ax   Do not set AXEnhancedUserInterface on Chromium & Electron apps (also disables retries).
      --tier1-retries N  Extra Tier 1 reads, 150 ms apart, after a failure in Chromium & Electron apps (default 0;
                         the B5 matrix measured 3 as a net loss).
      --max-text N       Truncate logged text to N characters (default 200; 0 = unlimited).
      --help             Show this text.

    Exit codes: 0 ok · 1 --once found no text · 2 Accessibility not granted · 64 bad arguments.
    """

    static func parse(_ args: [String]) -> Result<Arguments, UsageError> {
        var parsed = Arguments()
        var iterator = args.makeIterator()
        while let arg = iterator.next() {
            switch arg {
            case "--once": parsed.once = true
            case "--no-enhanced-ax": parsed.enhancedAX = false
            case "--help", "-h": parsed.help = true
            case "--out":
                guard let value = iterator.next() else { return .failure(UsageError(message: "--out needs a file path")) }
                parsed.outPath = value
            case "--tiers":
                guard let value = iterator.next(), let tiers = CaptureOptions.parseTiers(value) else {
                    return .failure(UsageError(message: "--tiers needs a comma-separated subset of 1,2,3"))
                }
                parsed.tiers = tiers
            case "--tier1-retries":
                guard let value = iterator.next(), let n = Int(value), n >= 0 else {
                    return .failure(UsageError(message: "--tier1-retries needs a non-negative integer"))
                }
                parsed.tier1Retries = n
            case "--max-text":
                guard let value = iterator.next(), let n = Int(value), n >= 0 else {
                    return .failure(UsageError(message: "--max-text needs a non-negative integer"))
                }
                parsed.maxText = n
            default:
                return .failure(UsageError(message: "unknown argument: \(arg)"))
            }
        }
        return .success(parsed)
    }

    var captureOptions: CaptureOptions {
        CaptureOptions(tiers: tiers, enhancedAX: enhancedAX, tier1Retries: tier1Retries)
    }
}

