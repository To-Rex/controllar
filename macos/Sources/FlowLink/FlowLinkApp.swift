import AppKit
import FlowLinkKit
import SwiftUI

@main
struct FlowLinkApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @ObservedObject private var model = AppModel.shared

    var body: some Scene {
        MenuBarExtra {
            MenuBarContent(model: model)
        } label: {
            Image(systemName: model.menuBarSymbol)
        }
        .menuBarExtraStyle(.window)

        Window("FlowLink", id: "main") {
            MainWindow(model: model)
                .onAppear {
                    appDelegate.windowDidAppear()
                }
        }
        .defaultSize(width: 980, height: 660)
        .windowStyle(.hiddenTitleBar)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var autoClosedWindow = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        AppModel.shared.bootstrap()

        // `flowlink://connect?...` links (QR codes opened from browsers).
        NSAppleEventManager.shared().setEventHandler(
            self,
            andSelector: #selector(handleURLEvent(_:replyEvent:)),
            forEventClass: AEEventClass(0x4755524C), // kInternetEventClass 'GURL'
            andEventID: AEEventID(0x4755524C) // kAEGetURL 'GURL'
        )

        // Launch into the menu bar only: close the (auto-opened) main window
        // for returning users; keep it for first-run onboarding.
        if AppModel.shared.onboarded {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak self] in
                guard let self, !self.autoClosedWindow else { return }
                self.autoClosedWindow = true
                for window in NSApp.windows where window.canBecomeMain {
                    window.close()
                }
            }
        }

        // Design-verification mode: render the main screens and exit.
        if let dir = ProcessInfo.processInfo.environment["FLOWLINK_SNAPSHOT_DIR"] {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                Snapshot.renderAll(model: AppModel.shared, directory: dir)
                NSApp.terminate(nil)
            }
        }
    }

    func windowDidAppear() {
        autoClosedWindow = true
    }

    func applicationWillTerminate(_ notification: Notification) {
        AppModel.shared.shutdown()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        true
    }

    @objc private func handleURLEvent(_ event: NSAppleEventDescriptor, replyEvent: NSAppleEventDescriptor) {
        // The URL arrives as the event's direct object ('----').
        let directObject = AEKeyword(0x2D2D2D2D)
        guard let value = event.paramDescriptor(forKeyword: directObject)?.stringValue else { return }
        DispatchQueue.main.async {
            AppModel.shared.connectLink(value)
        }
    }
}
