import Foundation

public struct StrictJSONDocument: Equatable, Sendable {
    public let root: StrictJSONValue

    public static func decode(
        _ data: Data,
        maximumBytes: Int,
        maximumDepth: Int
    ) throws -> StrictJSONDocument {
        guard maximumBytes > 0,
              maximumDepth > 0,
              !data.isEmpty,
              data.count <= maximumBytes,
              !data.starts(with: [0xef, 0xbb, 0xbf])
        else { throw BackendError.backendProtocolError }

        var parser = StrictJSONParser(
            bytes: Array(data),
            maximumDepth: maximumDepth
        )
        let root = try parser.parseDocument()
        return StrictJSONDocument(root: root)
    }
}

public indirect enum StrictJSONValue: Equatable, Sendable {
    case object(StrictJSONObject)
    case array([StrictJSONValue])
    case string(String)
    case integer(Int)
    case double(Double)
    case boolean(Bool)
    case null

    public func exactObject(
        keys expectedKeys: [String]
    ) throws -> [String: StrictJSONValue] {
        guard case let .object(object) = self,
              Set(expectedKeys).count == expectedKeys.count,
              object.members.count == expectedKeys.count,
              object.members.allSatisfy({ !$0.keyWasEscaped }),
              Set(object.members.map(\.key)) == Set(expectedKeys)
        else { throw BackendError.backendProtocolError }

        return Dictionary(
            uniqueKeysWithValues: object.members.map { ($0.key, $0.value) }
        )
    }

    public func exactArray() throws -> [StrictJSONValue] {
        guard case let .array(value) = self
        else { throw BackendError.backendProtocolError }
        return value
    }

    public func exactString() throws -> String {
        guard case let .string(value) = self
        else { throw BackendError.backendProtocolError }
        return value
    }

    public func exactInteger() throws -> Int {
        guard case let .integer(value) = self
        else { throw BackendError.backendProtocolError }
        return value
    }

    public func exactDouble() throws -> Double {
        guard case let .double(value) = self
        else { throw BackendError.backendProtocolError }
        return value
    }

    public func exactBoolean() throws -> Bool {
        guard case let .boolean(value) = self
        else { throw BackendError.backendProtocolError }
        return value
    }

    public func exactNull() throws {
        guard case .null = self
        else { throw BackendError.backendProtocolError }
    }
}

public struct StrictJSONObject: Equatable, Sendable {
    fileprivate let members: [StrictJSONObjectMember]
}

private struct StrictJSONObjectMember: Equatable, Sendable {
    let key: String
    let keyWasEscaped: Bool
    let value: StrictJSONValue
}

private struct StrictJSONParser {
    private let bytes: [UInt8]
    private let maximumDepth: Int
    private var index = 0

    init(bytes: [UInt8], maximumDepth: Int) {
        self.bytes = bytes
        self.maximumDepth = maximumDepth
    }

    mutating func parseDocument() throws -> StrictJSONValue {
        skipWhitespace()
        let value = try parseValue(depth: 0)
        skipWhitespace()
        guard index == bytes.count
        else { throw BackendError.backendProtocolError }
        return value
    }

    private mutating func parseValue(depth: Int) throws -> StrictJSONValue {
        guard let byte = currentByte
        else { throw BackendError.backendProtocolError }

        switch byte {
        case 0x7b:
            guard depth < maximumDepth
            else { throw BackendError.backendProtocolError }
            return try parseObject(depth: depth + 1)
        case 0x5b:
            guard depth < maximumDepth
            else { throw BackendError.backendProtocolError }
            return try parseArray(depth: depth + 1)
        case 0x22:
            return .string(try parseString().value)
        case 0x74:
            try consumeKeyword([0x74, 0x72, 0x75, 0x65])
            return .boolean(true)
        case 0x66:
            try consumeKeyword([0x66, 0x61, 0x6c, 0x73, 0x65])
            return .boolean(false)
        case 0x6e:
            try consumeKeyword([0x6e, 0x75, 0x6c, 0x6c])
            return .null
        case 0x2d, 0x30...0x39:
            return try parseNumber()
        default:
            throw BackendError.backendProtocolError
        }
    }

    private mutating func parseObject(depth: Int) throws -> StrictJSONValue {
        try consume(0x7b)
        skipWhitespace()
        if consumeIfPresent(0x7d) {
            return .object(StrictJSONObject(members: []))
        }

        var members: [StrictJSONObjectMember] = []
        var seenKeys: Set<String> = []
        while true {
            guard currentByte == 0x22
            else { throw BackendError.backendProtocolError }
            let key = try parseString()
            guard seenKeys.insert(key.value).inserted
            else { throw BackendError.backendProtocolError }
            skipWhitespace()
            try consume(0x3a)
            skipWhitespace()
            let value = try parseValue(depth: depth)
            members.append(
                StrictJSONObjectMember(
                    key: key.value,
                    keyWasEscaped: key.hadEscape,
                    value: value
                )
            )
            skipWhitespace()
            if consumeIfPresent(0x7d) {
                break
            }
            try consume(0x2c)
            skipWhitespace()
        }
        return .object(StrictJSONObject(members: members))
    }

    private mutating func parseArray(depth: Int) throws -> StrictJSONValue {
        try consume(0x5b)
        skipWhitespace()
        if consumeIfPresent(0x5d) {
            return .array([])
        }

        var values: [StrictJSONValue] = []
        while true {
            values.append(try parseValue(depth: depth))
            skipWhitespace()
            if consumeIfPresent(0x5d) {
                break
            }
            try consume(0x2c)
            skipWhitespace()
        }
        return .array(values)
    }

    private mutating func parseString() throws -> (value: String, hadEscape: Bool) {
        try consume(0x22)
        var decoded: [UInt8] = []
        var hadEscape = false

        while let byte = currentByte {
            index += 1
            if byte == 0x22 {
                guard let value = String(bytes: decoded, encoding: .utf8)
                else { throw BackendError.backendProtocolError }
                return (value, hadEscape)
            }
            guard byte >= 0x20
            else { throw BackendError.backendProtocolError }
            if byte != 0x5c {
                decoded.append(byte)
                continue
            }

            hadEscape = true
            guard let escape = currentByte
            else { throw BackendError.backendProtocolError }
            index += 1
            switch escape {
            case 0x22, 0x5c, 0x2f:
                decoded.append(escape)
            case 0x62:
                decoded.append(0x08)
            case 0x66:
                decoded.append(0x0c)
            case 0x6e:
                decoded.append(0x0a)
            case 0x72:
                decoded.append(0x0d)
            case 0x74:
                decoded.append(0x09)
            case 0x75:
                let first = try parseHexQuad()
                let scalar: UInt32
                if (0xd800...0xdbff).contains(first) {
                    guard consumeIfPresent(0x5c), consumeIfPresent(0x75)
                    else { throw BackendError.backendProtocolError }
                    let second = try parseHexQuad()
                    guard (0xdc00...0xdfff).contains(second)
                    else { throw BackendError.backendProtocolError }
                    scalar = 0x10000
                        + ((first - 0xd800) << 10)
                        + (second - 0xdc00)
                } else {
                    guard !(0xdc00...0xdfff).contains(first)
                    else { throw BackendError.backendProtocolError }
                    scalar = first
                }
                guard let unicodeScalar = UnicodeScalar(scalar)
                else { throw BackendError.backendProtocolError }
                decoded.append(contentsOf: String(unicodeScalar).utf8)
            default:
                throw BackendError.backendProtocolError
            }
        }
        throw BackendError.backendProtocolError
    }

    private mutating func parseHexQuad() throws -> UInt32 {
        var value: UInt32 = 0
        for _ in 0..<4 {
            guard let byte = currentByte, let digit = hexadecimalValue(of: byte)
            else { throw BackendError.backendProtocolError }
            index += 1
            value = value * 16 + digit
        }
        return value
    }

    private func hexadecimalValue(of byte: UInt8) -> UInt32? {
        switch byte {
        case 0x30...0x39:
            return UInt32(byte - 0x30)
        case 0x41...0x46:
            return UInt32(byte - 0x41 + 10)
        case 0x61...0x66:
            return UInt32(byte - 0x61 + 10)
        default:
            return nil
        }
    }

    private mutating func parseNumber() throws -> StrictJSONValue {
        let start = index
        _ = consumeIfPresent(0x2d)
        guard let firstDigit = currentByte
        else { throw BackendError.backendProtocolError }
        if firstDigit == 0x30 {
            index += 1
        } else {
            guard (0x31...0x39).contains(firstDigit)
            else { throw BackendError.backendProtocolError }
            consumeDigits()
        }

        var isDouble = false
        if consumeIfPresent(0x2e) {
            isDouble = true
            guard currentByte.map({ (0x30...0x39).contains($0) }) == true
            else { throw BackendError.backendProtocolError }
            consumeDigits()
        }
        if currentByte == 0x65 || currentByte == 0x45 {
            isDouble = true
            index += 1
            if currentByte == 0x2b || currentByte == 0x2d {
                index += 1
            }
            guard currentByte.map({ (0x30...0x39).contains($0) }) == true
            else { throw BackendError.backendProtocolError }
            consumeDigits()
        }

        let source = String(decoding: bytes[start..<index], as: UTF8.self)
        if isDouble {
            guard let value = Double(source), value.isFinite
            else { throw BackendError.backendProtocolError }
            return .double(value)
        }
        if let value = Int(source) {
            return .integer(value)
        }
        guard let value = Double(source), value.isFinite
        else { throw BackendError.backendProtocolError }
        return .double(value)
    }

    private mutating func consumeDigits() {
        while currentByte.map({ (0x30...0x39).contains($0) }) == true {
            index += 1
        }
    }

    private mutating func consumeKeyword(_ keyword: [UInt8]) throws {
        guard bytes[index...].starts(with: keyword)
        else { throw BackendError.backendProtocolError }
        index += keyword.count
    }

    private mutating func skipWhitespace() {
        while let byte = currentByte,
              byte == 0x20 || byte == 0x09 || byte == 0x0a || byte == 0x0d {
            index += 1
        }
    }

    private mutating func consume(_ expected: UInt8) throws {
        guard consumeIfPresent(expected)
        else { throw BackendError.backendProtocolError }
    }

    private mutating func consumeIfPresent(_ expected: UInt8) -> Bool {
        guard currentByte == expected else { return false }
        index += 1
        return true
    }

    private var currentByte: UInt8? {
        guard index < bytes.count else { return nil }
        return bytes[index]
    }
}
