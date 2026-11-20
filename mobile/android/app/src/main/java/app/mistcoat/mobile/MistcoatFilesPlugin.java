package app.mistcoat.mobile;

import android.app.Activity;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.DocumentsContract;
import android.provider.MediaStore;

import androidx.activity.result.ActivityResult;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;

/**
 * Saving protected images where the user chooses, through Android's own
 * pickers (the Storage Access Framework), so the app needs no storage
 * permission at all.
 *
 *   saveAs({file})              "Save as" dialog: any folder, SD card, Drive...
 *   pickFolder()                choose a folder once; access is kept
 *   saveToFolder({folder, files})  write into that folder, no dialog
 *   saveToGallery({files})      Pictures/Mistcoat (Android 10+)
 *
 * Files arrive as paths in the app cache (written by the Filesystem plugin),
 * so large images never cross the JavaScript bridge as base64 twice.
 */
@CapacitorPlugin(name = "MistcoatFiles")
public class MistcoatFilesPlugin extends Plugin {

    // ------------------------------------------------------------ save as

    @PluginMethod
    public void saveAs(PluginCall call) {
        JSObject file = call.getObject("file");
        if (file == null) { call.reject("No file given"); return; }
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType(file.getString("mime", "image/png"));
        intent.putExtra(Intent.EXTRA_TITLE, file.getString("name", "protected.png"));
        startActivityForResult(call, intent, "savedAs");
    }

    @ActivityCallback
    private void savedAs(PluginCall call, ActivityResult result) {
        if (result.getResultCode() != Activity.RESULT_OK || result.getData() == null
                || result.getData().getData() == null) {
            call.reject("Cancelled", "CANCELLED");
            return;
        }
        Uri target = result.getData().getData();
        JSObject file = call.getObject("file");
        try {
            copy(cacheFile(file.getString("path")), target);
            JSObject ret = new JSObject();
            ret.put("location", displayName(target));
            call.resolve(ret);
        } catch (IOException e) {
            call.reject("Could not save: " + e.getMessage(), e);
        }
    }

    // ------------------------------------------------------- chosen folder

    @PluginMethod
    public void pickFolder(PluginCall call) {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        startActivityForResult(call, intent, "folderPicked");
    }

    @ActivityCallback
    private void folderPicked(PluginCall call, ActivityResult result) {
        if (result.getResultCode() != Activity.RESULT_OK || result.getData() == null
                || result.getData().getData() == null) {
            call.reject("Cancelled", "CANCELLED");
            return;
        }
        Uri tree = result.getData().getData();
        // Keep access across restarts, so saving needs no dialog next time.
        getContext().getContentResolver().takePersistableUriPermission(tree,
                Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        JSObject ret = new JSObject();
        ret.put("folder", tree.toString());
        ret.put("name", folderName(tree));
        call.resolve(ret);
    }

    @PluginMethod
    public void saveToFolder(PluginCall call) {
        String folder = call.getString("folder");
        if (folder == null) { call.reject("No folder given"); return; }
        Uri tree = Uri.parse(folder);
        if (!hasAccess(tree)) {
            call.reject("Access to the folder was lost; choose it again", "NO_ACCESS");
            return;
        }
        ContentResolver resolver = getContext().getContentResolver();
        Uri parent = DocumentsContract.buildDocumentUriUsingTree(tree,
                DocumentsContract.getTreeDocumentId(tree));
        try {
            List<String> saved = new ArrayList<>();
            for (JSONObject f : files(call)) {
                // The provider renames on a clash ("name (1).png"); never overwrites.
                Uri doc = DocumentsContract.createDocument(resolver, parent,
                        f.optString("mime", "image/png"), f.optString("name", "protected.png"));
                if (doc == null) throw new IOException("the folder refused a new file");
                copy(cacheFile(f.optString("path")), doc);
                saved.add(displayName(doc));
            }
            JSObject ret = new JSObject();
            ret.put("saved", new JSArray(saved));
            ret.put("location", folderName(tree));
            call.resolve(ret);
        } catch (IOException | JSONException e) {
            call.reject("Could not save: " + e.getMessage(), e);
        }
    }

    // -------------------------------------------------------------- gallery

    @PluginMethod
    public void saveToGallery(PluginCall call) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            call.reject("Saving to the gallery needs Android 10 or newer", "UNSUPPORTED");
            return;
        }
        ContentResolver resolver = getContext().getContentResolver();
        try {
            List<String> saved = new ArrayList<>();
            for (JSONObject f : files(call)) {
                ContentValues v = new ContentValues();
                v.put(MediaStore.Images.Media.DISPLAY_NAME, f.optString("name", "protected.png"));
                v.put(MediaStore.Images.Media.MIME_TYPE, f.optString("mime", "image/png"));
                v.put(MediaStore.Images.Media.RELATIVE_PATH,
                        Environment.DIRECTORY_PICTURES + "/Mistcoat");
                v.put(MediaStore.Images.Media.IS_PENDING, 1);
                Uri item = resolver.insert(
                        MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), v);
                if (item == null) throw new IOException("the gallery refused a new image");
                copy(cacheFile(f.optString("path")), item);
                ContentValues done = new ContentValues();
                done.put(MediaStore.Images.Media.IS_PENDING, 0);
                resolver.update(item, done, null, null);
                saved.add(f.optString("name"));
            }
            JSObject ret = new JSObject();
            ret.put("saved", new JSArray(saved));
            ret.put("location", "Pictures/Mistcoat");
            call.resolve(ret);
        } catch (IOException | JSONException e) {
            call.reject("Could not save: " + e.getMessage(), e);
        }
    }

    // -------------------------------------------------------------- helpers

    private List<JSONObject> files(PluginCall call) throws JSONException {
        JSArray arr = call.getArray("files");
        List<JSONObject> out = new ArrayList<>();
        if (arr == null) return out;
        for (int i = 0; i < arr.length(); i++) out.add(arr.getJSONObject(i));
        return out;
    }

    /** A file the app wrote to its own cache, given as a file:// URI or path. */
    private File cacheFile(String path) throws IOException {
        if (path == null) throw new IOException("no source file");
        String p = path.startsWith("file:") ? Uri.parse(path).getPath() : path;
        File f = new File(p).getCanonicalFile();
        File cache = getContext().getCacheDir().getCanonicalFile();
        if (!f.getPath().startsWith(cache.getPath())) throw new IOException("source outside app cache");
        return f;
    }

    private void copy(File src, Uri dst) throws IOException {
        try (InputStream in = new FileInputStream(src);
             OutputStream out = getContext().getContentResolver().openOutputStream(dst, "w")) {
            if (out == null) throw new IOException("cannot write to the chosen location");
            byte[] buf = new byte[1 << 16];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        }
    }

    private boolean hasAccess(Uri tree) {
        for (android.content.UriPermission p : getContext().getContentResolver().getPersistedUriPermissions()) {
            if (p.getUri().equals(tree) && p.isWritePermission()) return true;
        }
        return false;
    }

    private String folderName(Uri tree) {
        Uri doc = DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree));
        String name = displayName(doc);
        return name != null ? name : tree.getLastPathSegment();
    }

    private String displayName(Uri uri) {
        try (Cursor c = getContext().getContentResolver().query(uri,
                new String[]{DocumentsContract.Document.COLUMN_DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst()) return c.getString(0);
        } catch (Exception ignored) {
            // Some providers don't answer; the name is only for the message.
        }
        return uri.getLastPathSegment();
    }
}
