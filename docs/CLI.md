# Using Mistcoat from other software

Mistcoat runs fully headless: every command below works without opening a
window, and on Windows without ever flashing a console. That makes it usable
as an add-on for an art or photo management program, a script, or a batch job.

There are two ways in:

| You want to… | Use |
|---|---|
| Protect images in the background and read the results | the **command line** (`protect`, or `serve` for many images) |
| Show the artist the Mistcoat window with images already queued | **open the app with files** |

## Which executable

| Platform | Command line | Opens the window |
|---|---|---|
| Windows | `Mistcoat.exe <command>` (windowed: never shows a console) or `mistcoat-cli.exe <command>` (console build, for terminals) | `Mistcoat.exe [files]` |
| macOS | `/Applications/Mistcoat.app/Contents/MacOS/Mistcoat <command>` | `open -a Mistcoat [files]` |
| Linux .deb / .rpm | `mistcoat <command>` | `mistcoat [files]` |
| Flatpak | `flatpak run app.mistcoat.Mistcoat <command>` | `flatpak run app.mistcoat.Mistcoat [files]` |
| Snap | `mistcoat <command>` (can read and write your home folder and removable media) | `mistcoat [files]` |
| AppImage | `./Mistcoat-…​.AppImage <command>` | `./Mistcoat-…​.AppImage [files]` |
| Python package | `mistcoat <command>` | `mistcoat-gui [files]` |

The first argument decides: a command name (`protect`, `report`, `passes`,
`info`, `serve`) or `--version`/`--help` runs headless; anything else opens
the app.

### Invisible on Windows

* **`Mistcoat.exe`** is a windowed program, so starting it can never open a
  console window, however the host starts it. In command mode it writes to
  whatever stdout/stderr the host gives it (pipes or files), so the host can
  read the JSON. Typed in a terminal, it prints to that terminal, but the
  terminal doesn't wait for it to finish.
* **`mistcoat-cli.exe`** is the console build: the right choice in a
  terminal or a `.bat`/PowerShell script. It doesn't load the windowing
  library (Qt), so it is half the size and starts faster. A program
  launching it hidden must ask Windows for no window (`CREATE_NO_WINDOW`,
  shown in the examples below), or a console window appears briefly.
* When the host cannot capture output at all, add `--json-file result.json
  --quiet` and read the file afterwards.

Both were checked by launching them the way a host does and watching for any
window or console: none appeared.

## Commands

```
mistcoat protect <images, folders or globs…> [options]
mistcoat report  <images…> [options]          measure only, writes nothing
mistcoat info    [--json]                     version, GPU, formats, presets
mistcoat passes  [--json]                     passes and presets
mistcoat serve                                JSON-lines server (see below)
mistcoat --version
```

Global options (before the command): `--gpu` / `--no-gpu`.

### `protect` options

| Option | Meaning |
|---|---|
| `-o DIR`, `--output DIR` | output folder. Default: a `Protected` folder next to each original |
| `--output-file FILE` | exact output file (one input only); the extension picks the format |
| `--on-exists rename\|overwrite\|skip\|error` | when the output exists. Default `rename` (`name (2).png`) |
| `--preset light\|balanced\|strong\|maximum` | protection level (default `balanced`) |
| `--strength 0-100` | override the preset's strength |
| `--enable PASS` / `--disable PASS` | switch individual passes (see `mistcoat passes`) |
| `--no-harden` | skip re-compression hardening (faster, less durable) |
| `--format source\|png\|jpeg\|webp\|tiff` | output format (default: same as the source, with the same colour mode, bit depth, ICC profile and resolution) |
| `--jpeg-quality 70-100` | for JPEG output |
| `--suffix TEXT` | file-name suffix with `-o` (default `_protected`) |
| `--credit NAME`, `--copyright TEXT` | embedded in the file's metadata |
| `--no-metadata` | do not embed the "do not train" declaration |
| `--quick` | skip the quality measurement (faster; `metrics` is null) |
| `--json` | print one JSON document on stdout instead of text |
| `--json-file FILE` | also write that JSON document to a file |
| `--progress none\|text\|json` | progress on stderr; `json` gives one JSON object per line |
| `--quiet` | no text output |

The original image is never overwritten, whatever `--on-exists` says.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | every image was protected (or skipped as asked) |
| 1 | at least one image failed; the others were still processed |
| 2 | bad arguments |
| 3 | no supported images found |

### JSON result

```json
{
  "mistcoat": "0.1.0",
  "command": "protect",
  "compute": "GPU (NVIDIA GeForce GTX 1050 Ti)",
  "settings": {"strength": 60.0, "passes": ["texture_warp", "dct_poison", "chroma", "hf_noise"],
               "harden": true, "format": "source", "metadata": true, "seed": 20260101},
  "ok": true,
  "results": [
    {
      "input": "D:\\Art\\piece.png",
      "output": "D:\\Art\\Protected\\piece_protected.png",
      "status": "ok",
      "error": null,
      "width": 4000, "height": 3000,
      "passes": ["texture_warp", "dct_poison", "chroma", "hf_noise"],
      "metrics": {"psnr_db": 39.5, "ssim": 0.99612, "peak_change": 8.0, "mean_change": 1.214,
                  "durability": 0.78, "visibility": "Invisible in normal viewing",
                  "durability_label": "Survives re-compression well"},
      "color": {"input": "16-bit RGB, Adobe RGB (1998)", "output": "16-bit RGB, Adobe RGB (1998)"},
      "note": null,
      "seconds": 39.2
    }
  ],
  "summary": {"total": 1, "succeeded": 1, "skipped": 0, "failed": 0}
}
```

`status` is `ok`, `skipped` or `failed` (with `error` set). `color` says
what the original was and what was written; they differ only when the chosen
format can't hold the original (CMYK to PNG, or 16-bit to JPEG). With `--progress
json`, stderr carries lines like
`{"event": "progress", "index": 1, "total": 3, "file": "piece.png", "stage": "Hardening against re-compression", "fraction": 0.7}`.

## `serve`: one process for many images

Starting Mistcoat costs a few seconds (more for the windowed exe, which
unpacks itself). For an add-on that protects images one at a time, keep one
`serve` process running and talk to it with one JSON object per line:

```
→ {"id": 1, "command": "protect", "input": "D:/Art/a.png", "output_file": "D:/Out/a.png", "preset": "strong", "progress": true}
← {"id": 1, "event": "progress", "stage": "Applying stroke disruption", "fraction": 0.0}
← {"id": 1, "ok": true, "result": { …one entry of the "results" list above… }}
→ {"id": 2, "command": "quit"}
```

The first line Mistcoat prints is `{"event": "ready", "mistcoat": "0.1.0"}`.
Commands: `protect` (fields as the options above, with underscores:
`output_file`, `output_dir`, `on_exists`, `preset`, `strength`, `enable`,
`disable`, `no_harden`, `format`, `jpeg_quality`, `credit`, `copyright`,
`no_metadata`, `quick`, `progress`), `report`, `info`, `ping`, `quit`. A bad
request gets `{"id": …, "ok": false, "error": "…"}` and the server keeps
running.

## Examples

**Python**

```python
import json, subprocess, sys

exe = r"C:\Program Files\Mistcoat\Mistcoat.exe"          # or "mistcoat"
flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
p = subprocess.run([exe, "protect", src, "--output-file", dst, "--preset", "strong", "--json"],
                   capture_output=True, text=True, creationflags=flags)
result = json.loads(p.stdout)
if p.returncode != 0:
    print(result["results"][0]["error"])
```

**C# / .NET**

```csharp
var psi = new ProcessStartInfo(@"C:\Program Files\Mistcoat\mistcoat-cli.exe") {
    UseShellExecute = false, CreateNoWindow = true,
    RedirectStandardOutput = true, RedirectStandardError = true,
};
foreach (var a in new[] { "protect", src, "--output-file", dst, "--json" }) psi.ArgumentList.Add(a);
using var p = Process.Start(psi)!;
string json = p.StandardOutput.ReadToEnd();
p.WaitForExit();
```

**PowerShell**

```powershell
$r = & .\mistcoat-cli.exe protect "D:\Art\*.png" -o "D:\Art\Protected" --json | ConvertFrom-Json
```

## Opening the app with files queued

```
Mistcoat a.png b.jpg "D:\Art\New series"          files and folders
Mistcoat --files-from selection.txt               one path per line
Mistcoat --files-from -                           the list on stdin
Mistcoat --new-window a.png                       do not reuse an open window
```

The images appear in the window's queue straight away, ready for the artist
to check the preview and press *Protect and save*. A list file is plain
UTF-8 text, one path per line. Blank lines and lines starting with `#` are
ignored, relative paths are relative to the list file, and quotes around a
path are fine. Use a list file for large selections: Windows limits a
command line to about 32,000 characters.

If Mistcoat is already open, a new launch with files hands them to that
window (it comes to the front) and exits, so an art manager's "Send to
Mistcoat" can be used repeatedly without stacking up windows. Missing files
and non-images are skipped, and the window's status line says how many.

On macOS, files opened with `open -a Mistcoat …`, dropped on the Dock icon,
or sent with *Open With* arrive the same way.

### Troubleshooting

The windowed app has no console to report problems to. Set the environment
variable `MISTCOAT_LOG` to a file path and it logs what it does there:
start-up, files handed to an open window, and any error.

### Hooking it into an art manager

Most asset managers and image viewers have an "external tool" or "open
with" setting that runs a program with the selected files:

* **Show the selection in Mistcoat:** program `Mistcoat.exe`, arguments
  `%files%` (the manager's placeholder for the selected paths), or
  `--files-from %listfile%` if it can write the selection to a list file.
* **Protect in the background:** program `Mistcoat.exe` (Windows) or
  `mistcoat`, arguments
  `protect %files% -o "<folder>" --preset balanced --json-file "<log>.json" --quiet`.
