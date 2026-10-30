import Darwin
import Foundation
import IOKit

/// Reads the machine. Nothing here needs more than the widget's sandbox, and
/// one read of all three takes well under a millisecond.
public enum SystemReader {
    /// How long one reading lasts. CPU and GPU jump from one moment to the next:
    /// against five seconds' mean, 0.5 s was off by 6 points on average for the
    /// CPU, 2 s by 2.7, and a single GPU read by 13 where eight in 2 s were by 4
    /// (measured under load, 2026-10-30).
    public static let window: Duration = .seconds(2)
    private static let gpuReads = 8

    /// One press of ↻ reloads every "맥 상태" on screen, one after another, and
    /// CPU and GPU move by tens of percent within seconds: they get one reading
    /// so that they agree.
    public static func read() async -> SystemStats {
        await shared.value()
    }

    private static let shared = SharedWork(keep: .seconds(10)) { await measure() }

    private static func measure() async -> SystemStats {
        let coresBefore = coreTicks()
        var gpu: [Int?] = []
        for _ in 0..<gpuReads {
            gpu.append(gpuPercent())
            try? await Task.sleep(for: window / gpuReads)
        }
        let cores = coreTicks()
        return SystemStats(
            cpu: coresBefore.flatMap { before in cores.flatMap { SystemMeters.cpuPercent(from: before, to: $0) } },
            gpu: SystemMeters.average(gpu),
            ram: ramPercent())
    }

    private static func coreTicks() -> [CoreTicks]? {
        let host = mach_host_self()
        defer { mach_port_deallocate(mach_task_self_, host) }  // each call takes a reference
        var coreCount: natural_t = 0
        var info: processor_info_array_t?
        var infoCount: mach_msg_type_number_t = 0
        guard host_processor_info(host, PROCESSOR_CPU_LOAD_INFO, &coreCount, &info, &infoCount) == KERN_SUCCESS,
              let info else { return nil }
        defer {
            vm_deallocate(mach_task_self_, vm_address_t(bitPattern: info),
                          vm_size_t(infoCount) * vm_size_t(MemoryLayout<integer_t>.stride))
        }
        return (0..<Int(coreCount)).map { core in
            let tick = { (state: Int32) in UInt32(bitPattern: info[core * Int(CPU_STATE_MAX) + Int(state)]) }
            return CoreTicks(user: tick(CPU_STATE_USER), system: tick(CPU_STATE_SYSTEM),
                             idle: tick(CPU_STATE_IDLE), nice: tick(CPU_STATE_NICE))
        }
    }

    private static func ramPercent() -> Int? {
        let host = mach_host_self()
        defer { mach_port_deallocate(mach_task_self_, host) }
        var stats = vm_statistics64()
        var count = mach_msg_type_number_t(MemoryLayout<vm_statistics64>.stride / MemoryLayout<integer_t>.stride)
        let result = withUnsafeMutablePointer(to: &stats) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                host_statistics64(host, HOST_VM_INFO64, $0, &count)
            }
        }
        guard result == KERN_SUCCESS else { return nil }
        let pages = MemoryPages(internalPages: UInt64(stats.internal_page_count), purgeable: UInt64(stats.purgeable_count),
                                wired: UInt64(stats.wire_count), compressed: UInt64(stats.compressor_page_count))
        return SystemMeters.ramPercent(pages, pageSize: UInt64(vm_kernel_page_size),
                                       physicalMemory: ProcessInfo.processInfo.physicalMemory)
    }

    private static func gpuPercent() -> Int? {
        var services: io_iterator_t = 0
        guard IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOAccelerator"), &services) == KERN_SUCCESS
        else { return nil }
        defer { IOObjectRelease(services) }
        while case let service = IOIteratorNext(services), service != 0 {
            defer { IOObjectRelease(service) }
            let property = IORegistryEntryCreateCFProperty(service, "PerformanceStatistics" as CFString,
                                                           kCFAllocatorDefault, 0)?.takeRetainedValue()
            if let statistics = property as? [String: Any], let percent = SystemMeters.gpuPercent(in: statistics) {
                return percent
            }
        }
        return nil
    }
}

/// Runs `work` once for everyone who asks while it is running, or within
/// `keep` after it ended.
actor SharedWork<Value: Sendable> {
    private let keep: Duration
    private let work: @Sendable () async -> Value
    private var running: Task<Value, Never>?
    private var last: (value: Value, at: ContinuousClock.Instant)?

    init(keep: Duration = .zero, work: @escaping @Sendable () async -> Value) {
        self.keep = keep
        self.work = work
    }

    func value() async -> Value {
        if let last, last.at.duration(to: .now) < keep { return last.value }
        if let running { return await running.value }
        let task = Task { await work() }
        running = task
        let value = await task.value
        running = nil
        last = (value, .now)
        return value
    }
}
