import Foundation

public struct LaunchHandshake: Equatable, Sendable {
    public let schemaVersion: Int
    public let origin: LocalOrigin

    public static func decode(_ line: Data) throws -> LaunchHandshake {
        guard (1...4_096).contains(line.count),
              !line.contains(0x0a),
              !line.contains(0x0d)
        else { throw BackendError.backendProtocolError }

        let document = try StrictJSONDocument.decode(
            line,
            maximumBytes: 4_096,
            maximumDepth: 4
        )
        let fields = try document.root.exactObject(
            keys: ["schema_version", "origin", "token"]
        )
        guard let versionValue = fields["schema_version"],
              let originValue = fields["origin"],
              let tokenValue = fields["token"]
        else { throw BackendError.backendProtocolError }
        let version = try versionValue.exactInteger()
        let origin = try originValue.exactString()
        let token = try tokenValue.exactString()
        guard version == 1
        else { throw BackendError.backendProtocolError }
        return LaunchHandshake(
            schemaVersion: version,
            origin: try LocalOrigin(origin: origin, token: token)
        )
    }
}
