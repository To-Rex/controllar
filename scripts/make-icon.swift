import Foundation

// Renders FlowLink's app icon (rounded-square gradient + twin arrows) at all
// iconset sizes. Called by scripts/build-macos.sh; no external deps.
// Usage: swift scripts/make-icon.swift <output-iconset-dir>

import AppKit
import CoreGraphics

let outDir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "FlowLink.iconset"
try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)

func drawIcon(size: CGFloat) -> NSImage {
    let image = NSImage(size: NSSize(width: size, height: size))
    image.lockFocus()
    guard let ctx = NSGraphicsContext.current?.cgContext else {
        image.unlockFocus()
        return image
    }

    let rect = CGRect(x: 0, y: 0, width: size, height: size)
    let radius = size * 0.225
    let path = CGPath(roundedRect: rect.insetBy(dx: size * 0.045, dy: size * 0.045),
                      cornerWidth: radius, cornerHeight: radius, transform: nil)

    // Background gradient.
    ctx.saveGState()
    ctx.addPath(path)
    ctx.clip()
    let colors = [
        CGColor(srgbRed: 0.40, green: 0.33, blue: 0.98, alpha: 1.0),
        CGColor(srgbRed: 0.58, green: 0.33, blue: 0.98, alpha: 1.0),
        CGColor(srgbRed: 0.66, green: 0.36, blue: 1.00, alpha: 1.0),
    ] as CFArray
    let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                              colors: colors, locations: [0, 0.55, 1])!
    ctx.drawLinearGradient(gradient,
                           start: CGPoint(x: 0, y: size),
                           end: CGPoint(x: size, y: 0),
                           options: [])

    // Subtle top gloss.
    let gloss = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                           colors: [CGColor(gray: 1, alpha: 0.22), CGColor(gray: 1, alpha: 0)] as CFArray,
                           locations: [0, 1])!
    ctx.drawLinearGradient(gloss,
                           start: CGPoint(x: size / 2, y: size),
                           end: CGPoint(x: size / 2, y: size * 0.45),
                           options: [])
    ctx.restoreGState()

    // Twin arrows (left and right).
    func arrow(pointingLeft: Bool) -> CGPath {
        let w = size * 0.30
        let h = size * 0.26
        let shaft = size * 0.055
        let cx = size * 0.5
        let cy = size * (pointingLeft ? 0.615 : 0.385)
        let p = CGMutablePath()
        let dir: CGFloat = pointingLeft ? -1 : 1
        // Shaft
        p.addRoundedRect(in: CGRect(x: cx - w / 2, y: cy - shaft / 2, width: w, height: shaft),
                         cornerWidth: shaft / 2, cornerHeight: shaft / 2)
        // Head
        let tipX = cx + dir * w / 2
        let backX = cx + dir * (w / 2 - h / 2)
        p.move(to: CGPoint(x: tipX, y: cy))
        p.addLine(to: CGPoint(x: backX, y: cy + h / 2))
        p.addLine(to: CGPoint(x: backX, y: cy - h / 2))
        p.closeSubpath()
        return p
    }

    ctx.saveGState()
    ctx.setFillColor(CGColor(gray: 1, alpha: 0.97))
    ctx.addPath(arrow(pointingLeft: true))
    ctx.addPath(arrow(pointingLeft: false))
    ctx.fillPath()
    ctx.restoreGState()

    image.unlockFocus()
    return image
}

func writePNG(_ image: NSImage, pixels: Int, to path: String) {
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil,
                                     pixelsWide: pixels, pixelsHigh: pixels,
                                     bitsPerSample: 8, samplesPerPixel: 4,
                                     hasAlpha: true, isPlanar: false,
                                     colorSpaceName: .deviceRGB,
                                     bytesPerRow: 0, bitsPerPixel: 0) else { return }
    rep.size = NSSize(width: pixels, height: pixels)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let size = CGFloat(pixels)
    NSImage(size: NSSize(width: size, height: size)).size = .zero
    drawIcon(size: size).draw(in: NSRect(x: 0, y: 0, width: size, height: size))
    NSGraphicsContext.restoreGraphicsState()
    guard let data = rep.representation(using: .png, properties: [:]) else { return }
    try? data.write(to: URL(fileURLWithPath: path))
}

let variants: [(name: String, pixels: Int)] = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]

for variant in variants {
    writePNG(drawIcon(size: CGFloat(variant.pixels)), pixels: variant.pixels,
             to: "\(outDir)/\(variant.name)")
}
print("icon set written to \(outDir)")
