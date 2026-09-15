import CaptureKit
import Foundation

// MARK: - Flags

struct UsageError: Error {
    let message: String
}

struct Arguments {
    /// Accepted for contract v1 compatibility; running once is now the only mode.
    var once = false
    var tiers: [Int] = [1, 2, 3]
    var enhancedAX = true
    var tier1Retries = 0
    var maxText = 200
    var help = false

    static let usage = """
    usage: capture-spike [--once] [--tiers 1,2,3] [--no-enhanced-ax] [--tier1-retries N] [--max-text N] [--help]

    Native capture primitive for ContextAI. Runs Tier 1 (AX kAXSelectedText) → Tier 2 (AX press on
    Edit > Copy) → Tier 3 (synthetic ⌘C) once on the frontmost app, then the bounds chain, and prints one
    JSON line to stdout. The contract is docs/capture-contract.md; callers pass `--once --max-text 0`.

      --once             Accepted for compatibility: running once is the only mode.
      --tiers 1,2,3      Which tiers to run; they always run in ascending order 1 → 2 → 3 (default: 1,2,3).
      --no-enhanced-ax   Do not set AXEnhancedUserInterface on Chromium & Electron apps (also disables retries).
      --tier1-retries N  Extra Tier 1 reads, 150 ms apart, after a failure in Chromium & Electron apps (default 0;
                         the B5 matrix measured 3 as a net loss).
      --max-text N       Truncate the text to N characters (default 200; 0 = unlimited — what the app passes).
      --help             Show this text.

    Exit codes: 0 text captured · 1 no text · 2 Accessibility not granted · 64 bad arguments.
    """

    static func parse(_ args: [String]) -> Result<Arguments, UsageError> {
        var parsed = Arguments()
        var iterator = args.makeIterator()
        while let arg = iterator.next() {
            switch arg {
            case "--once": parsed.once = true
            case "--no-enhanced-ax": parsed.enhancedAX = false
            case "--help", "-h": parsed.help = true
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
