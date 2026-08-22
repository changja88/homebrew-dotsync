import Foundation

struct ExecutableFileInspection: Sendable {
    let resolvedURL: URL
    let isRegularFile: Bool
    let isExecutable: Bool
}

protocol ExecutableFileSystem: Sendable {
    func inspectExecutable(at url: URL) throws -> ExecutableFileInspection?
}

private struct LocalExecutableFileSystem: ExecutableFileSystem {
    func inspectExecutable(at url: URL) throws -> ExecutableFileInspection? {
        guard url.isFileURL else { return nil }
        let resolvedURL = url.standardizedFileURL.resolvingSymlinksInPath()
        let attributes = try FileManager.default.attributesOfItem(
            atPath: resolvedURL.path
        )
        return ExecutableFileInspection(
            resolvedURL: resolvedURL,
            isRegularFile: attributes[.type] as? FileAttributeType == .typeRegular,
            isExecutable: FileManager.default.isExecutableFile(
                atPath: resolvedURL.path
            )
        )
    }
}

public struct BackendExecutableResolver: Sendable {
    private struct Candidate: Sendable {
        let executable: URL
        let cellarRoot: URL
    }

    private let fileSystem: any ExecutableFileSystem
    private let fixedCandidates = [
        Candidate(
            executable: URL(fileURLWithPath: "/opt/homebrew/bin/dotsync"),
            cellarRoot: URL(
                fileURLWithPath: "/opt/homebrew/Cellar/dotsync",
                isDirectory: true
            )
        ),
        Candidate(
            executable: URL(fileURLWithPath: "/usr/local/bin/dotsync"),
            cellarRoot: URL(
                fileURLWithPath: "/usr/local/Cellar/dotsync",
                isDirectory: true
            )
        ),
    ]

    public init() {
        fileSystem = LocalExecutableFileSystem()
    }

    init(fileSystem: any ExecutableFileSystem) {
        self.fileSystem = fileSystem
    }

    public func resolve(testOverride: URL? = nil) throws -> URL {
        if let testOverride {
            return try validateInjectedTestExecutable(testOverride)
        }
        for candidate in fixedCandidates {
            if let executable = validateHomebrewCandidate(candidate) {
                return executable
            }
        }
        throw BackendError.backendNotFound
    }

    private func validateInjectedTestExecutable(_ url: URL) throws -> URL {
        guard url.isFileURL,
              url.path.hasPrefix("/"),
              let inspection = try? fileSystem.inspectExecutable(at: url),
              inspection.isRegularFile,
              inspection.isExecutable,
              inspection.resolvedURL.standardizedFileURL
                == url.standardizedFileURL
        else { throw BackendError.backendNotFound }
        return inspection.resolvedURL
    }

    private func validateHomebrewCandidate(_ candidate: Candidate) -> URL? {
        guard let inspection = try? fileSystem.inspectExecutable(
            at: candidate.executable
        ),
        inspection.isRegularFile,
        inspection.isExecutable,
        isDescendant(inspection.resolvedURL, of: candidate.cellarRoot)
        else { return nil }
        return inspection.resolvedURL
    }

    private func isDescendant(_ url: URL, of directory: URL) -> Bool {
        let targetComponents = url.standardizedFileURL.pathComponents
        let rootComponents = directory.standardizedFileURL.pathComponents
        guard targetComponents.count > rootComponents.count
        else { return false }
        return targetComponents.prefix(rootComponents.count)
            .elementsEqual(rootComponents)
    }
}
