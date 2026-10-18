import CFlowLinkCore

/// Thin Swift access to the Rust core version.
public enum CoreVersion {
    public static var string: String {
        guard let cString = flowlink_version() else { return "unknown" }
        return String(cString: cString)
    }
}
