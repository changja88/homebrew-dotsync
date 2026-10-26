import Foundation

/// Calls back when one of `files` changes on disk, whoever wrote it. It
/// watches their folder: an atomic write replaces a file, so a watch on the
/// file itself would go deaf after the first save. Other files changing in
/// the folder call nothing. A file rewritten in place, not replaced, leaves
/// the folder as it was and goes unnoticed.
public final class FolderWatch {
    private let source: DispatchSourceFileSystemObject
    /// Read and written on the source's queue only.
    private var stamps: [URL: Stamp]

    public init?(folder: URL, files: [URL], queue: DispatchQueue,
                 onChange: @escaping @Sendable () -> Void) {
        let descriptor = open(folder.path, O_EVTONLY)
        guard descriptor >= 0 else { return nil }
        source = DispatchSource.makeFileSystemObjectSource(fileDescriptor: descriptor, eventMask: .write,
                                                           queue: queue)
        stamps = Self.stamps(of: files)
        source.setEventHandler { [weak self] in
            guard let self else { return }
            let now = Self.stamps(of: files)
            guard now != stamps else { return }
            stamps = now
            onChange()
        }
        source.setCancelHandler { close(descriptor) }
        source.resume()
    }

    deinit {
        source.cancel()
    }

    /// A replaced file has a new file number even when its date ties.
    private struct Stamp: Equatable {
        let modified: Date?
        let fileNumber: Int?
    }

    private static func stamps(of files: [URL]) -> [URL: Stamp] {
        Dictionary(uniqueKeysWithValues: files.map { url in
            let attributes = try? FileManager.default.attributesOfItem(atPath: url.path)
            return (url, Stamp(modified: attributes?[.modificationDate] as? Date,
                               fileNumber: attributes?[.systemFileNumber] as? Int))
        })
    }
}
