import AppKit
import Testing
@testable import CaptureKit

@Suite("PasteboardSnapshot", .serialized)
@MainActor
struct PasteboardSnapshotTests {
    private func makePasteboard() -> NSPasteboard {
        NSPasteboard(name: NSPasteboard.Name("com.contextai.capturekit.tests.\(UUID().uuidString)"))
    }

    private func richItems() -> [NSPasteboardItem] {
        let rtf = NSPasteboardItem()
        let attributed = NSAttributedString(string: "Hello", attributes: [.font: NSFont.boldSystemFont(ofSize: 12)])
        let rtfData = try! attributed.data(from: NSRange(location: 0, length: 5), documentAttributes: [.documentType: NSAttributedString.DocumentType.rtf])
        rtf.setData(rtfData, forType: .rtf)
        rtf.setString("Hello", forType: .string)

        let image = NSPasteboardItem()
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 2, pixelsHigh: 2, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        image.setData(bitmap.representation(using: .png, properties: [:])!, forType: .png)
        return [rtf, image]
    }

    @Test("RTF + image round-trip: identical items, types and bytes after restore")
    func roundTrip() {
        let pasteboard = makePasteboard()
        defer { pasteboard.releaseGlobally() }
        pasteboard.clearContents()
        pasteboard.writeObjects(richItems())

        let before = PasteboardSnapshot.take(from: pasteboard)
        #expect(before.items.count == 2)
        // The pasteboard may add translated flavours (e.g. utf16 plain text); the declared ones must be there.
        #expect(before.items[0].types.contains(NSPasteboard.PasteboardType.rtf.rawValue))
        #expect(before.items[0].types.contains(NSPasteboard.PasteboardType.string.rawValue))
        #expect(before.items[1].types == [NSPasteboard.PasteboardType.png.rawValue])
        #expect(before.items.allSatisfy { Set($0.data.keys) == Set($0.types) })

        // Simulate a capture clobbering the clipboard with plain text.
        pasteboard.clearContents()
        pasteboard.setString("captured text", forType: .string)
        #expect(pasteboard.pasteboardItems?.count == 1)

        before.restore(to: pasteboard)
        let after = PasteboardSnapshot.take(from: pasteboard)
        #expect(after.items == before.items)
        #expect(pasteboard.string(forType: .string) == "Hello")
    }

    @Test("empty pasteboard restores to empty")
    func emptyRoundTrip() {
        let pasteboard = makePasteboard()
        defer { pasteboard.releaseGlobally() }
        pasteboard.clearContents()

        let before = PasteboardSnapshot.take(from: pasteboard)
        #expect(before.items.isEmpty)
        pasteboard.setString("junk", forType: .string)
        before.restore(to: pasteboard)
        #expect((pasteboard.pasteboardItems ?? []).isEmpty)
    }

    @Test("restoreIfChanged leaves an untouched pasteboard alone and restores a changed one")
    func restoreIfChanged() {
        let pasteboard = makePasteboard()
        defer { pasteboard.releaseGlobally() }
        pasteboard.clearContents()
        pasteboard.writeObjects(richItems())

        let snapshot = PasteboardSnapshot.take(from: pasteboard)
        let untouchedCount = pasteboard.changeCount
        #expect(snapshot.restoreIfChanged(to: pasteboard) == false)
        #expect(pasteboard.changeCount == untouchedCount)
        #expect(pasteboard.pasteboardItems?.count == 2)

        pasteboard.clearContents()
        pasteboard.setString("clobbered", forType: .string)
        #expect(snapshot.restoreIfChanged(to: pasteboard) == true)
        #expect(pasteboard.string(forType: .string) == "Hello")
        #expect(PasteboardSnapshot.take(from: pasteboard).items == snapshot.items)
    }

    @Test("waitForChange reports true only when changeCount advances")
    func waitForChange() async {
        let pasteboard = makePasteboard()
        defer { pasteboard.releaseGlobally() }
        let baseline = pasteboard.clearContents()

        let stale = await PasteboardWait.forChange(on: pasteboard, from: baseline, timeout: .milliseconds(30))
        #expect(stale == false)

        // Real copies clear (bumping changeCount) and then write, exactly like a target app's Copy does.
        pasteboard.clearContents()
        pasteboard.setString("new", forType: .string)
        let advanced = await PasteboardWait.forChange(on: pasteboard, from: baseline, timeout: .milliseconds(30))
        #expect(advanced == true)
    }
}
