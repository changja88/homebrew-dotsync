import Darwin
import Foundation
import IOKit

/// Reads the machine. Nothing here needs more than the widget's sandbox, and
/// one read of all three takes well under a millisecond.
public enum SystemReader {
    /// CPU use is the difference between two reads this far apart.
    public static let cpuWindow: Duration = .milliseconds(500)

    /// Widgets reloading together get one reading: a second read of the GPU
    /// close behind another would say 0, at both of its two reads.
    public static func read() async -> SystemStats {
        await shared.value()
    }

    private static let shared = SharedWork { await measure() }

    private static func measure() async -> SystemStats {
        let coresBefore = coreTicks()
        let gpuBefore = gpuPercent()
        try? await Task.sleep(for: cpuWindow)
        let cores = coreTicks()
        return SystemStats(
            cpu: coresBefore.flatMap { before in cores.flatMap { SystemMeters.cpuPercent(from: before, to: $0) } },
            gpu: SystemMeters.larger(gpuBefore, gpuPercent()),
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

/// Runs `work` once for everyone who asks while it is running.
actor SharedWork<Value: Sendable> {
    private let work: @Sendable () async -> Value
    private var running: Task<Value, Never>?

    init(work: @escaping @Sendable () async -> Value) {
        self.work = work
    }

    func value() async -> Value {
        if let running { return await running.value }
        let task = Task { await work() }
        running = task
        let value = await task.value
        running = nil
        return value
    }
}
