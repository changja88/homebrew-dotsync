import Foundation

/// What the "맥 상태" widget shows, in percent; nil where a value could not be
/// read, which it draws as "—".
public struct SystemStats: Equatable, Sendable {
    public var cpu: Int?
    public var gpu: Int?
    public var ram: Int?

    public static let unknown = SystemStats()

    public init(cpu: Int? = nil, gpu: Int? = nil, ram: Int? = nil) {
        self.cpu = cpu
        self.gpu = gpu
        self.ram = ram
    }
}

/// One core's time counters since boot, which wrap around at 2³².
public struct CoreTicks: Equatable, Sendable {
    public var user: UInt32
    public var system: UInt32
    public var idle: UInt32
    public var nice: UInt32

    public init(user: UInt32, system: UInt32, idle: UInt32, nice: UInt32) {
        self.user = user
        self.system = system
        self.idle = idle
        self.nice = nice
    }
}

public struct MemoryPages: Equatable, Sendable {
    public var internalPages: UInt64
    public var purgeable: UInt64
    public var wired: UInt64
    public var compressed: UInt64

    public init(internalPages: UInt64, purgeable: UInt64, wired: UInt64, compressed: UInt64) {
        self.internalPages = internalPages
        self.purgeable = purgeable
        self.wired = wired
        self.compressed = compressed
    }
}

/// From raw counters to percents. A number that cannot be right is nil, never
/// a clamped value that would pass for a reading.
public enum SystemMeters {
    /// The share of all cores' time that was busy between two reads.
    public static func cpuPercent(from before: [CoreTicks], to after: [CoreTicks]) -> Int? {
        guard before.count == after.count else { return nil }
        var busy: UInt64 = 0
        var total: UInt64 = 0
        for (old, new) in zip(before, after) {
            let worked = UInt64(new.user &- old.user) + UInt64(new.system &- old.system) + UInt64(new.nice &- old.nice)
            busy += worked
            total += worked + UInt64(new.idle &- old.idle)
        }
        return percent(busy, of: total)
    }

    /// Activity Monitor's 사용된 메모리 (app memory, wired and compressed) of
    /// all the machine's memory.
    public static func ramPercent(_ pages: MemoryPages, pageSize: UInt64, physicalMemory: UInt64) -> Int? {
        guard pages.internalPages >= pages.purgeable else { return nil }
        let app = pages.internalPages - pages.purgeable
        return percent((app + pages.wired + pages.compressed) * pageSize, of: physicalMemory)
    }

    /// The GPU's value in its accelerator's `PerformanceStatistics`.
    public static func gpuPercent(in statistics: [String: Any]) -> Int? {
        guard let value = statistics["Device Utilization %"] as? Int, (0...100).contains(value) else { return nil }
        return value
    }

    /// Two reads of the GPU. One read right after another (measured: within
    /// tens of ms) says 0, and the widget's last reading may have just ended.
    public static func larger(_ first: Int?, _ second: Int?) -> Int? {
        [first, second].compactMap { $0 }.max()
    }

    private static func percent(_ part: UInt64, of whole: UInt64) -> Int? {
        guard whole > 0, part <= whole else { return nil }
        return Int((Double(part) / Double(whole) * 100).rounded())
    }
}
