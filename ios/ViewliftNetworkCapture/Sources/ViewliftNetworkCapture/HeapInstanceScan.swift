#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Darwin
import Foundation
import ObjectiveC.runtime

/// Finds live instances of one class (and its subclasses) on the heap.
/// Buffers are allocated before the walk because the recorder runs with zones locked.
enum HeapInstanceScan {
    static func instances(of target: AnyClass, limit: Int) -> [AnyObject] {
        let classes = matchingClasses(target)
        guard !classes.isEmpty, limit > 0 else { return [] }
        let sorted = UnsafeMutablePointer<UInt>.allocate(capacity: classes.count)
        let sizes = UnsafeMutablePointer<Int>.allocate(capacity: classes.count)
        let found = UnsafeMutablePointer<UInt>.allocate(capacity: limit)
        defer {
            sorted.deallocate()
            sizes.deallocate()
            found.deallocate()
        }
        for (index, item) in classes.enumerated() {
            sorted[index] = item.address
            sizes[index] = item.size
        }
        var state = InstanceScanState(
            classes: sorted, sizes: sizes, total: classes.count,
            found: found, capacity: limit, count: 0, isaMask: HeapCensus.isaMask()
        )
        withUnsafeMutablePointer(to: &state) { pointer in
            HeapCensus.enumerateZones(context: UnsafeMutableRawPointer(pointer), recorder: instanceRecorder)
        }
        return (0..<state.count).compactMap { index in
            guard let pointer = UnsafeRawPointer(bitPattern: found[index]) else { return nil }
            return Unmanaged<AnyObject>.fromOpaque(pointer).takeUnretainedValue()
        }
    }

    /// Classes whose superclass chain reaches `target`, found without messaging any class.
    private static func matchingClasses(_ target: AnyClass) -> [(address: UInt, size: Int)] {
        var listCount: UInt32 = 0
        guard let list = objc_copyClassList(&listCount) else { return [] }
        let raw = UnsafeMutableRawPointer(list)
        defer { free(raw) }
        let pointers = raw.assumingMemoryBound(to: UnsafeRawPointer.self)
        let targetAddress = UInt(bitPattern: unsafeBitCast(target, to: UnsafeRawPointer.self))
        var out: [(address: UInt, size: Int)] = []
        for index in 0..<Int(listCount) {
            let pointer = pointers[index]
            var current: AnyClass? = unsafeBitCast(pointer, to: AnyClass.self)
            while let item = current {
                if UInt(bitPattern: unsafeBitCast(item, to: UnsafeRawPointer.self)) == targetAddress {
                    let cls: AnyClass = unsafeBitCast(pointer, to: AnyClass.self)
                    out.append((UInt(bitPattern: pointer), max(16, class_getInstanceSize(cls))))
                    break
                }
                current = class_getSuperclass(item)
            }
        }
        return out.sorted { $0.address < $1.address }
    }
}

private struct InstanceScanState {
    let classes: UnsafeMutablePointer<UInt>
    let sizes: UnsafeMutablePointer<Int>
    let total: Int
    let found: UnsafeMutablePointer<UInt>
    let capacity: Int
    var count: Int
    let isaMask: UInt
}

private func instanceRecorder(
    _ task: task_t,
    _ context: UnsafeMutableRawPointer?,
    _ type: UInt32,
    _ ranges: UnsafeMutablePointer<vm_range_t>?,
    _ rangeCount: UInt32
) {
    guard let context, let ranges else { return }
    let state = context.assumingMemoryBound(to: InstanceScanState.self)
    let limit = Int(rangeCount)
    var index = 0
    while index < limit, state.pointee.count < state.pointee.capacity {
        let range = ranges.advanced(by: index).pointee
        index += 1
        guard range.size >= 16, let block = UnsafeRawPointer(bitPattern: range.address) else { continue }
        let isa = block.load(as: UInt.self) & state.pointee.isaMask
        var low = 0
        var high = state.pointee.total - 1
        while low <= high {
            let mid = (low + high) >> 1
            let value = state.pointee.classes[mid]
            if value == isa {
                if Int(clamping: range.size) >= state.pointee.sizes[mid] {
                    state.pointee.found[state.pointee.count] = UInt(range.address)
                    state.pointee.count += 1
                }
                break
            }
            if value < isa {
                low = mid + 1
            } else {
                high = mid - 1
            }
        }
    }
}
#endif
