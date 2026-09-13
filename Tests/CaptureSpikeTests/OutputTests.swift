import Foundation
import Testing
@testable import capture_spike

@Suite("Output")
@MainActor
struct OutputTests {
    @Test("appends to a file that already has content, keeping the original at the front")
    func appends() throws {
        let path = FileManager.default.temporaryDirectory
            .appendingPathComponent("capture-spike-output-\(UUID().uuidString).jsonl").path
        defer { try? FileManager.default.removeItem(atPath: path) }
        try "{\"first\":1}\n".write(toFile: path, atomically: true, encoding: .utf8)

        let output = try Output(path: path)
        output.emit("{\"second\":2}")
        output.close()

        let contents = try String(contentsOfFile: path, encoding: .utf8)
        #expect(contents == "{\"first\":1}\n{\"second\":2}\n")
    }

    @Test("creates the file when it does not exist")
    func creates() throws {
        let path = FileManager.default.temporaryDirectory
            .appendingPathComponent("capture-spike-output-\(UUID().uuidString).jsonl").path
        defer { try? FileManager.default.removeItem(atPath: path) }

        let output = try Output(path: path)
        output.emit("line")
        output.close()
        #expect(try String(contentsOfFile: path, encoding: .utf8) == "line\n")
    }

    @Test("no path means stdout only")
    func stdoutOnly() throws {
        let output = try Output(path: nil)
        output.emit("line")
        output.close()
    }
}
