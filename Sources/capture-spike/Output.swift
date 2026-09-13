import Foundation

func stderr(_ message: String) {
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

/// Where JSON lines go: always stdout, plus an optional file opened for appending.
@MainActor
final class Output {
    private let file: FileHandle?
    private var reportedFileFailure = false

    init(path: String?) throws {
        guard let path else { file = nil; return }
        if !FileManager.default.fileExists(atPath: path) {
            FileManager.default.createFile(atPath: path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: URL(fileURLWithPath: path))
        try handle.seekToEnd()
        file = handle
    }

    func emit(_ line: String) {
        print(line)
        fflush(stdout)
        guard let file else { return }
        do {
            try file.write(contentsOf: Data((line + "\n").utf8))
        } catch {
            if !reportedFileFailure {
                reportedFileFailure = true
                stderr("capture-spike: could not write to --out file: \(error.localizedDescription)")
            }
        }
    }

    func close() {
        try? file?.close()
    }
}
