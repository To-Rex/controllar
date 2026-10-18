import AppKit
import SwiftUI

/// Offscreen renderer used for design verification and previews.
///
/// Driven by the `FLOWLINK_SNAPSHOT_DIR` environment variable (see
/// `FlowLinkApp.delegate`): renders the main screens to PNG files and exits.
@MainActor
public enum Snapshot {
    public static func renderView<V: View>(_ view: V, size: CGSize, scale: CGFloat = 2, to path: String) {
        // Render through a real hosting view inside an offscreen window —
        // ScrollViews and materials only lay out correctly in a window.
        let hosting = NSHostingView(rootView: view.frame(width: size.width, height: size.height))
        hosting.frame = CGRect(origin: .zero, size: size)

        let window = NSWindow(
            contentRect: CGRect(x: -20000, y: -20000, width: size.width, height: size.height),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        window.isReleasedWhenClosed = false
        window.contentView = hosting
        window.contentView?.layoutSubtreeIfNeeded()
        hosting.layoutSubtreeIfNeeded()
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))

        guard let rep = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) else {
            FileHandle.standardError.write(Data("snapshot: no bitmap for \(path)\n".utf8))
            return
        }
        hosting.cacheDisplay(in: hosting.bounds, to: rep)

        // Rescale to the requested pixel density.
        let targetPixels = NSSize(width: size.width * scale, height: size.height * scale)
        guard let scaled = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: Int(targetPixels.width),
            pixelsHigh: Int(targetPixels.height),
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .deviceRGB,
            bytesPerRow: 0,
            bitsPerPixel: 0
        ) else { return }
        scaled.size = targetPixels
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: scaled)
        NSImage(size: targetPixels).size = .zero
        NSImage(cgImage: rep.cgImage!, size: size).draw(in: NSRect(origin: .zero, size: targetPixels))
        NSGraphicsContext.restoreGraphicsState()

        guard let png = scaled.representation(using: .png, properties: [:]) else { return }
        try? png.write(to: URL(fileURLWithPath: path))
        FileHandle.standardError.write(Data("snapshot: wrote \(path)\n".utf8))
    }

    /// Renders the key screens for the app model in `directory`.
    public static func renderAll(model: AppModel, directory: String) {
        let window = CGSize(width: 980, height: 660)
        renderView(MainWindow(model: model), size: window, to: "\(directory)/main.png")
        renderView(
            DashboardView(model: model).background(DS.bg),
            size: CGSize(width: 760, height: 640),
            to: "\(directory)/dashboard.png"
        )
        renderView(
            SettingsView(model: model).background(DS.bg),
            size: CGSize(width: 760, height: 900),
            to: "\(directory)/settings.png"
        )
        renderView(
            MenuBarContent(model: model).background(DS.bg),
            size: CGSize(width: 320, height: 420),
            to: "\(directory)/menubar.png"
        )
    }
}
