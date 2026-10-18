import CoreGraphics
import Foundation

/// Geometry of the union of all active displays, in the global CoreGraphics
/// coordinate space (the same space `CGEvent.location` uses).
public struct ScreenMetrics: Equatable {
    public var width: Double
    public var height: Double
    public var origin: CGPoint
    public var scale: Double

    public static let fallback = ScreenMetrics(
        width: 1920, height: 1080, origin: .zero, scale: 1.0
    )

    /// Convert a global CG point into core coordinates (origin 0,0 at the
    /// top-left of the union frame).
    public func normalized(_ point: CGPoint) -> CGPoint {
        CGPoint(x: point.x - origin.x, y: point.y - origin.y)
    }

    /// Convert core coordinates back to a global CG point.
    public func global(_ point: CGPoint) -> CGPoint {
        CGPoint(x: point.x + origin.x, y: point.y + origin.y)
    }
}

public enum ScreenGeometry {
    /// Union region of all active displays.
    public static func unionMetrics() -> ScreenMetrics {
        var count: UInt32 = 0
        guard CGGetActiveDisplayList(0, nil, &count) == .success, count > 0 else {
            return .fallback
        }
        var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
        guard CGGetActiveDisplayList(count, &ids, &count) == .success else {
            return .fallback
        }
        var union = CGRect.null
        for id in ids.prefix(Int(count)) {
            union = union.union(CGDisplayBounds(id))
        }
        guard !union.isNull, union.width > 0, union.height > 0 else {
            return .fallback
        }
        return ScreenMetrics(
            width: Double(union.width),
            height: Double(union.height),
            origin: union.origin,
            scale: 1.0
        )
    }
}
