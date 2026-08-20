import CoreGraphics
import AppKit
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

@main
struct HermesWindowCapture {
    static func main() async {
        _ = NSApplication.shared
        guard CommandLine.arguments.count == 2 else {
            fputs("usage: capture_hermes_window.swift OUTPUT.png\n", stderr)
            exit(2)
        }
        let output = URL(fileURLWithPath: CommandLine.arguments[1])
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(
                true,
                onScreenWindowsOnly: true
            )
            let candidates = content.windows.filter { window in
                window.owningApplication?.bundleIdentifier == "com.nousresearch.hermes"
                    && window.frame.width > 800
                    && window.frame.height > 500
            }
            guard let window = candidates.max(by: {
                $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height
            }) else {
                throw NSError(domain: "HermesCapture", code: 4, userInfo: [NSLocalizedDescriptionKey: "Hermes window not found"])
            }
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let configuration = SCStreamConfiguration()
            configuration.width = Int(window.frame.width * 2)
            configuration.height = Int(window.frame.height * 2)
            configuration.showsCursor = false
            configuration.captureResolution = .best
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: filter,
                configuration: configuration
            )
            guard let destination = CGImageDestinationCreateWithURL(
                output as CFURL,
                UTType.png.identifier as CFString,
                1,
                nil
            ) else {
                throw NSError(domain: "HermesCapture", code: 6, userInfo: [NSLocalizedDescriptionKey: "unable to create PNG destination"])
            }
            CGImageDestinationAddImage(destination, image, nil)
            guard CGImageDestinationFinalize(destination) else {
                throw NSError(domain: "HermesCapture", code: 7, userInfo: [NSLocalizedDescriptionKey: "unable to finalize PNG"])
            }
            print(output.path)
        } catch {
            fputs("\(error)\n", stderr)
            exit(1)
        }
    }
}
