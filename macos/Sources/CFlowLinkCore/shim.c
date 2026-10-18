/*
 * SwiftPM requires at least one compilable source in a C target; the actual
 * implementation is the Rust static library linked into the executable.
 */
int flowlink_c_shim_anchor(void) { return 0; }
