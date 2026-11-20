import Capacitor
import Photos
import UIKit
import UniformTypeIdentifiers

/// Saving protected images where the user chooses, with the system's own UI.
///
///   saveAs({file}) / saveToFolder({files})  the "Save to Files" picker:
///       iCloud Drive, On My iPhone, any folder or connected drive
///   saveToGallery({files})                   straight into Photos
///
/// iOS does not let an app keep writing into a folder without asking, so
/// there is no remembered folder here; the picker opens at the last place
/// used. Files arrive as paths in the app cache (written by Filesystem).
@objc(MistcoatFilesPlugin)
public class MistcoatFilesPlugin: CAPPlugin, CAPBridgedPlugin, UIDocumentPickerDelegate {
    public let identifier = "MistcoatFilesPlugin"
    public let jsName = "MistcoatFiles"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "saveAs", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "saveToFolder", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "saveToGallery", returnType: CAPPluginReturnPromise),
    ]

    private var pendingCall: CAPPluginCall?

    // MARK: Save to Files

    @objc func saveAs(_ call: CAPPluginCall) {
        guard let file = call.getObject("file") else { return call.reject("No file given") }
        export([file], call)
    }

    @objc func saveToFolder(_ call: CAPPluginCall) {
        export(call.getArray("files", JSObject.self) ?? [], call)
    }

    private func export(_ files: [JSObject], _ call: CAPPluginCall) {
        let urls = files.compactMap { cacheURL($0["path"] as? String) }
        guard !urls.isEmpty, urls.count == files.count else {
            return call.reject("Source file missing")
        }
        DispatchQueue.main.async {
            let picker = UIDocumentPickerViewController(forExporting: urls, asCopy: true)
            picker.delegate = self
            picker.modalPresentationStyle = .formSheet
            self.pendingCall = call
            self.bridge?.viewController?.present(picker, animated: true)
        }
    }

    public func documentPicker(_ controller: UIDocumentPickerViewController,
                               didPickDocumentsAt urls: [URL]) {
        guard let call = pendingCall else { return }
        pendingCall = nil
        let folder = urls.first?.deletingLastPathComponent().lastPathComponent ?? "Files"
        call.resolve(["saved": urls.map { $0.lastPathComponent }, "location": folder])
    }

    public func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
        pendingCall?.reject("Cancelled", "CANCELLED")
        pendingCall = nil
    }

    // MARK: Photos

    @objc func saveToGallery(_ call: CAPPluginCall) {
        let urls = (call.getArray("files", JSObject.self) ?? []).compactMap { cacheURL($0["path"] as? String) }
        guard !urls.isEmpty else { return call.reject("Source file missing") }
        PHPhotoLibrary.requestAuthorization(for: .addOnly) { status in
            guard status == .authorized || status == .limited else {
                return call.reject("Mistcoat may not add to Photos. Allow it in Settings.", "NO_ACCESS")
            }
            PHPhotoLibrary.shared().performChanges({
                for url in urls {
                    // Added as a file, not re-encoded: the protection and the
                    // embedded "do not train" metadata stay intact.
                    PHAssetCreationRequest.forAsset().addResource(with: .photo, fileURL: url, options: nil)
                }
            }) { ok, error in
                if ok {
                    call.resolve(["saved": urls.map { $0.lastPathComponent }, "location": "Photos"])
                } else {
                    call.reject("Could not save to Photos: \(error?.localizedDescription ?? "unknown error")")
                }
            }
        }
    }

    // MARK: Helpers

    /// Only files the app itself wrote to its caches directory.
    private func cacheURL(_ path: String?) -> URL? {
        guard let path = path else { return nil }
        let url = (path.hasPrefix("file:") ? URL(string: path) : URL(fileURLWithPath: path))?.standardizedFileURL
        guard let url = url,
              let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first?.standardizedFileURL,
              url.path.hasPrefix(caches.path),
              FileManager.default.fileExists(atPath: url.path) else { return nil }
        return url
    }
}

/// The app's root view controller: Capacitor's, plus the app-local plugin.
class MistcoatViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(MistcoatFilesPlugin())
    }
}
