//! Single-instance guard.
//!
//! Two copies of FlowLink fight over the listen port (the second one ends up
//! in an error state with no pairing code — a confusing failure we can avoid
//! entirely). A named kernel mutex gives an atomic check across processes.

use windows::core::w;
use windows::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS};
use windows::Win32::System::Threading::CreateMutexW;
use windows::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONINFORMATION, MB_OK};

/// Returns `true` when this process should continue; shows a message box and
/// returns `false` when another instance already owns the mutex.
pub fn acquire() -> bool {
    // Safety: plain Win32 call with a static name.
    match unsafe { CreateMutexW(None, true, w!("FlowLink.SingleInstance")) } {
        Ok(handle) => {
            // CreateMutexW succeeds even when the mutex exists; the error
            // code tells us which is which.
            if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
                let _ = unsafe {
                    MessageBoxW(
                        None,
                        w!("FlowLink is already running.\n\nLook for the ⇄ icon in the system tray (it may be hidden behind the chevron)."),
                        w!("FlowLink"),
                        MB_OK | MB_ICONINFORMATION,
                    )
                };
                return false;
            }
            // `HANDLE` has no destructor in the windows crate, so simply
            // keeping it alive leaves the mutex owned until process exit —
            // exactly what we need.
            let _ = handle;
            true
        }
        // If the mutex cannot be created at all, don't block startup.
        Err(_) => true,
    }
}
