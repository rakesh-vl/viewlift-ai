#if DEBUG || VIEWLIFT_CAPTURE_ENABLED
import Darwin
import Foundation
import ObjectiveC.runtime

/// Counts live heap instances per class by walking every malloc zone.
/// Zones are locked during the walk, so the range callback must never allocate.
enum HeapCensus {
    struct Row: Equatable {
        let className: String
        let count: Int
        let instanceBytes: Int
    }

    /// System classes that commonly hold large memory in OTT apps.
    static let notableSystemClasses: Set<String> = [
        "UIImage", "UIImageView", "AVPlayer", "AVQueuePlayer", "AVPlayerItem",
        "AVPlayerLayer", "AVURLAsset", "WKWebView", "UIViewController",
        "UINavigationController", "UITableView", "UICollectionView", "CADisplayLink"
    ]

    static func snapshot(include: (_ className: String, _ imagePath: String) -> Bool = appClasses) -> [Row] {
        guard let table = ClassTable(include: include) else { return [] }
        defer { table.release() }
        var state = CensusState(
            classes: table.classes,
            sizes: table.sizes,
            counts: table.counts,
            total: table.total,
            isaMask: isaMask()
        )
        withUnsafeMutablePointer(to: &state) { pointer in
            enumerateZones(context: UnsafeMutableRawPointer(pointer))
        }
        return table.rows()
    }

    static func appClasses(className: String, imagePath: String) -> Bool {
        if notableSystemClasses.contains(className) { return true }
        let bundle = Bundle.main.bundlePath
        return !bundle.isEmpty && imagePath.hasPrefix(bundle)
    }

    private static func enumerateZones(context: UnsafeMutableRawPointer) {
        var addresses: UnsafeMutablePointer<vm_address_t>?
        var zoneCount: UInt32 = 0
        // TASK_NULL with an identity reader, as FLEX does; the self task makes some zones lock again.
        let task = task_t(TASK_NULL)
        guard malloc_get_all_zones(task, censusReader, &addresses, &zoneCount) == KERN_SUCCESS,
              let addresses else {
            return
        }
        for index in 0..<Int(zoneCount) {
            guard let zone = UnsafeMutablePointer<malloc_zone_t>(bitPattern: addresses[index]),
                  let introspect = zone.pointee.introspect,
                  let enumerator = introspect.pointee.enumerator else {
                continue
            }
            introspect.pointee.force_lock?(zone)
            _ = enumerator(task, context, UInt32(MALLOC_PTR_IN_USE_RANGE_TYPE), addresses[index],
                           censusReader, censusRecorder)
            introspect.pointee.force_unlock?(zone)
        }
    }

    private static func isaMask() -> UInt {
        let rtldDefault = UnsafeMutableRawPointer(bitPattern: -2)
        guard let symbol = dlsym(rtldDefault, "objc_debug_isa_class_mask") else { return UInt.max }
        return symbol.assumingMemoryBound(to: UInt.self).pointee
    }
}

private struct CensusState {
    let classes: UnsafeMutablePointer<UInt>
    let sizes: UnsafeMutablePointer<Int>
    let counts: UnsafeMutablePointer<Int>
    let total: Int
    let isaMask: UInt
}

private func censusReader(
    _ task: task_t,
    _ address: vm_address_t,
    _ size: vm_size_t,
    _ local: UnsafeMutablePointer<UnsafeMutableRawPointer?>?
) -> kern_return_t {
    local?.pointee = UnsafeMutableRawPointer(bitPattern: address)
    return KERN_SUCCESS
}

private func censusRecorder(
    _ task: task_t,
    _ context: UnsafeMutableRawPointer?,
    _ type: UInt32,
    _ ranges: UnsafeMutablePointer<vm_range_t>?,
    _ rangeCount: UInt32
) {
    guard let context, let ranges else { return }
    let state = context.assumingMemoryBound(to: CensusState.self)
    let classes = state.pointee.classes
    let total = state.pointee.total
    let mask = state.pointee.isaMask
    let limit = Int(rangeCount)
    // Unspecialized `for in` iterators allocate in debug builds; plain while loops do not.
    var index = 0
    while index < limit {
        let range = ranges.advanced(by: index).pointee
        index += 1
        guard range.size >= 16, let block = UnsafeRawPointer(bitPattern: range.address) else { continue }
        let isa = block.load(as: UInt.self) & mask
        var low = 0
        var high = total - 1
        while low <= high {
            let mid = (low + high) >> 1
            let value = classes[mid]
            if value == isa {
                if Int(range.size) >= state.pointee.sizes[mid] {
                    state.pointee.counts[mid] += 1
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

/// Sorted class pointers with parallel size and count buffers, allocated before zones lock.
private final class ClassTable {
    private struct Entry {
        let address: UInt
        let size: Int
        let name: String
    }

    let classes: UnsafeMutablePointer<UInt>
    let sizes: UnsafeMutablePointer<Int>
    let counts: UnsafeMutablePointer<Int>
    let names: [String]
    let total: Int

    init?(include: (String, String) -> Bool) {
        var listCount: UInt32 = 0
        guard let list = objc_copyClassList(&listCount) else { return nil }
        let raw = UnsafeMutableRawPointer(list)
        defer { free(raw) }
        // Reading through the autoreleasing pointer retains each class, and some root classes abort on retain.
        let pointers = raw.assumingMemoryBound(to: UnsafeRawPointer.self)
        var picked: [Entry] = []
        for index in 0..<Int(listCount) {
            let pointer = pointers[index]
            let cls: AnyClass = unsafeBitCast(pointer, to: AnyClass.self)
            let name = String(cString: class_getName(cls))
            let image = class_getImageName(cls).map { String(cString: $0) } ?? ""
            guard include(name, image) else { continue }
            let size = max(16, class_getInstanceSize(cls))
            picked.append(Entry(address: UInt(bitPattern: pointer), size: size, name: name))
        }
        guard !picked.isEmpty else { return nil }
        picked.sort { $0.address < $1.address }
        total = picked.count
        classes = .allocate(capacity: total)
        sizes = .allocate(capacity: total)
        counts = .allocate(capacity: total)
        for (index, item) in picked.enumerated() {
            classes[index] = item.address
            sizes[index] = item.size
            counts[index] = 0
        }
        names = picked.map(\.name)
    }

    func rows() -> [HeapCensus.Row] {
        (0..<total).compactMap { index in
            guard counts[index] > 0 else { return nil }
            return HeapCensus.Row(className: names[index], count: counts[index], instanceBytes: sizes[index])
        }
    }

    func release() {
        classes.deallocate()
        sizes.deallocate()
        counts.deallocate()
    }
}
#endif
