import Foundation

/// Swift-side mirrors of the C constants in `flowlink.h`.
///
/// Swift imports C anonymous-enum constants as `Int`, which does not match
/// the `Int32`/`UInt8` struct fields — these typed mirrors keep call sites
/// clear and compiler-checked. Keep in sync with the header.
public enum FLModeC {
    public static let client: Int32 = 0
    public static let server: Int32 = 1
}

/// `FlHostInput.kind` values (shell → core).
public enum FLHostKindC {
    public static let mouseMove: Int32 = 0
    public static let key: Int32 = 1
    public static let mouseButton: Int32 = 2
    public static let scroll: Int32 = 3
}

/// `FlInputEvent.kind` values (core → shell).
public enum FLInputKindC {
    public static let enter: Int32 = 0
    public static let key: Int32 = 1
    public static let mouseMove: Int32 = 2
    public static let mouseButton: Int32 = 3
    public static let scroll: Int32 = 4
    public static let releaseAll: Int32 = 5
}

/// Modifier bits.
public enum FLModC {
    public static let shift: UInt8 = 1
    public static let ctrl: UInt8 = 2
    public static let alt: UInt8 = 4
    public static let meta: UInt8 = 8
    public static let all: [UInt8] = [shift, ctrl, alt, meta]
}

/// Edge values (`FlInputEvent.flags` for Enter; `flowlink_set_edge`).
public enum FLEdgeC {
    public static let left: Int32 = 0
    public static let right: Int32 = 1
    public static let top: Int32 = 2
    public static let bottom: Int32 = 3
}
