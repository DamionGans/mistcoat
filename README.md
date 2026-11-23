# Mistcoat

Protect artwork from AI training and style mimicry. Mistcoat makes small,
mostly invisible changes to an image that degrade its value as training data,
and embeds the standard machine-readable "do not train" declaration.

Runs on **Windows**, **macOS**, **Linux**, **Android**, **iOS** and in any modern browser.
Each version uses its platform's own look: Windows 11 controls on Windows, the
native Mac interface on macOS, the desktop's theme on Linux, Apple's design on
iPhone and iPad, and Material 3 on Android, all following the system's light
or dark mode.
Everything happens on the device. No image is ever uploaded anywhere.

> A *mist coat* is the thinned first coat a decorator puts on bare plaster to
> seal it before the real paint goes on. It's a thin protective layer you don't
> really see.

---

## What it does, honestly

**No tool can make an image impossible to train on.** Glaze and Nightshade,
the research tools this category is measured against, have both been partly
defeated in published work, and Mistcoat will be too. What it does is raise
the cost and spoil the result:

| Pass | What it disrupts | Visible? |
|---|---|---|
| Stroke disruption | Style mimicry: a sub-pixel displacement field scrambles the stroke and edge statistics a model reads as your hand. It is geometric, so denoising can't remove it. | Subtle |
| Compression-resistant pattern | Writes the perturbation into the 8×8 DCT coefficients that JPEG and WebP preserve, so it survives a platform's re-encode. | Invisible |
| Colour-channel perturbation | Uses the eye's low colour resolution to place a larger change in chroma than luma would allow. | Invisible |
| Texture noise | Band-limited noise hidden in existing detail, degrading fine-texture learning. | Invisible |
| Ghost contours *(off by default)* | Faint synthetic edges at object scale that confuse shape recognition and captioning. | Visible up close |

Plus **re-compression hardening**: the perturbation is projected onto what
survives JPEG, WebP and downscaling, so it is still there after a gallery or
social site processes the upload.

### The numbers in the app

Every preview shows four figures. They're there so the artist can trust the
slider, not to reassure:

- **Visible change / PSNR / peak change**: how much the file actually changed.
  A hard per-pixel ceiling, tied to the strength slider, guarantees that
  "Light" stays invisible whichever passes are on.
- **Durability**: how much of the intended perturbation survives re-encoding.
  It is measured against **held-out** transforms (JPEG q85 and q60, WebP,
  downscale + JPEG) that the hardening step never optimises for, and it is
  normalised so it cannot be inflated by shrinking the perturbation.

On the bundled test painting:

| Preset | PSNR | Peak change | Durability |
|---|---|---|---|
| Light | 45.8 dB | 6/255 | 72% |
| Balanced | 39.5 dB | 8/255 | 78% |
| Strong | 37.0 dB | 10/255 | 80% |
| Maximum | 34.6 dB | 11/255 | 85% |

### Metadata

Outputs carry the IPTC/PLUS **Data Mining** property
(`DMI-PROHIBITED-AIMLTRAINING`) plus matching usage terms, and the artist's
name and copyright if given. Any scraper can strip this, so it is a legal and
policy signal rather than a technical defence, but it costs nothing.

Mistcoat deliberately does **not** write a tag saying "protected by
Mistcoat". A marker like that would help anyone building a filter to exclude
protected images from a dataset.

### Always keep the unprotected originals

Upload only the protected copies.

---

## Using it

**Desktop (Windows / macOS / Linux):** open Mistcoat, drop images or a folder onto
the window, pick a protection level, check the preview (drag the divider; hold
Space to see the original; double-click for 100%), then **Protect and save**.
Output goes to a `Protected` folder next to the originals unless you choose
another.

**Phone / tablet:** tap **Choose images** (or photograph a physical piece),
pick a level, check the preview (*Hold to compare*, *View 1:1*), then
**Protect and save**. You choose where it goes: any folder (the system's own
save picker), Photos / the gallery, or a folder you set once under
*Advanced*; **Share** sends it to another app instead.

**Colour:** a protected copy comes out the way the original went in. By
default it has the same format, colour mode, bit depth, ICC profile and
resolution, and the preview shows the true colours on your screen.

| | Desktop | Phone / tablet |
|---|---|---|
| Colour modes | RGB, greyscale, CMYK, Lab | RGB |
| Bit depth | 8 and 16 bits per channel (16-bit PNG and TIFF stay 16-bit) | 8 bits |
| Profiles kept | any ICC profile (sRGB, Adobe RGB, Display P3, ProPhoto, scanner, camera, printer), plus Adobe RGB from camera EXIF and PNG gamma/chromaticity | sRGB, Display P3, Adobe RGB, ProPhoto and other matrix RGB profiles; anything else becomes Display P3 |
| Preview | converted to the monitor's own profile | shown in Display P3 on screens that have it |

CMYK and Lab images are protected through a wide RGB working space, and
only the change is converted back. The black channel, ink limits and pure
black text stay exactly as they were. A format that can't hold the original
mode gets the nearest equivalent, with the matching profile. For example,
CMYK saved as PNG becomes RGB, and a phone saves HEIC as JPEG in Display P3.
Palette and 1-bit images are saved as full colour and greyscale.

**Visible watermark (optional, off by default):** writes text such as
"SAMPLE" or "© your name – art sample" into the picture, so anyone who sees
it knows it is a sample. It can be repeated across the picture, placed once
in the centre, or put small in a corner, at an opacity you choose. It is
drawn before the protection, so it is protected along with the rest. Three
levels set how hard it is to remove:

* **Plain:** an even grid of identical marks. Neatest, but watermark-removal
  tools handle this kind easily.
* **Strong** (default): every mark differs slightly in position, angle, size
  and opacity, with a soft, textured edge, so there is no single shape for a
  removal tool to learn.
* **Maximum:** as Strong, plus a second row of smaller marks, and the
  lettering is woven into the picture's texture, so removing the colour still
  leaves it visible in the detail. Busier to look at.

No visible watermark is impossible to remove: a determined person can paint
it out by hand. These levels make that slower and leave traces.

**Command line and add-ons:** everything also runs headless, without a
window (and on Windows without a console flashing up), with JSON output for
other programs:

```powershell
mistcoat protect "D:\Art\2026" -o "D:\Art\2026-protected" --preset strong
```

```powershell
mistcoat protect piece.png --output-file piece-safe.png --json
```

The app can also be opened with files (or a list file) already in its queue,
and a second launch hands its files to the open window. See
[docs/CLI.md](docs/CLI.md) for using Mistcoat from an art-management program.

---

## Project layout

```
desktop/                 Windows, macOS and Linux app (Python)
  pyproject.toml         Package metadata, dependencies, entry points, pytest
  src/mistcoat/          The package (src layout)
    core/                Reference engine: passes, pipeline, quality, metadata
    gui/                 PySide6 interface and per-platform look (theme.py)
    engines/             Optional add-on engines (planned)
    resources/           Window icon, shipped as package data
    cli.py               Command-line interface
  tests/                 Engine and UI-wording tests
  packaging/
    pyinstaller/         Shared PyInstaller spec and frozen entry point
    windows/  macos/     Build script, icon and version info per OS
    linux/               Build script, desktop entry, AppStream, nfpm, Flatpak
mobile/                  Android, iOS and web app (Capacitor)
  www/                   The app: plain ES modules, no build step
    engine/              JavaScript port of the engine (same constants and order)
    platform.js/.css     iOS (HIG) and Android (Material 3) look, picked at start
  test/                  Engine tests, incl. a cross-check against the Python engine
  scripts/serve.mjs      Local static server
  android/  ios/         Native projects generated by Capacitor
assets/                  Brand master icon
samples/                 Test painting shared by both test suites
scripts/make_icons.py    Regenerates every icon and splash on every platform
docs/                    Release and signing guide
.github/workflows/       CI: tests and builds for every platform
```

The Python engine is the reference. The JavaScript engine mirrors it pass for
pass. The random streams differ, so outputs match in character and measured
cost rather than bit for bit, and a test checks they agree on the sample
painting.

## Development

Desktop (Python 3.11+), from the `desktop` folder:

```powershell
cd desktop; py -m venv .venv
```

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

```powershell
.\.venv\Scripts\python.exe -m pytest
```

```powershell
.\.venv\Scripts\mistcoat-gui.exe
```

The editable install also provides the `mistcoat` command-line tool.

Mobile / web (Node 20+):

```powershell
cd mobile; npm install; npm test
```

```powershell
cd mobile; npm run serve
```

…then open <http://localhost:5317>.

## Building

| Target | Where | Command | Output |
|---|---|---|---|
| Windows (x64 or ARM64, built natively) | Windows | `desktop\packaging\windows\build.ps1` | `desktop\dist\Mistcoat.exe` |
| macOS | a Mac | `./desktop/packaging/macos/build.sh` | `desktop/dist/Mistcoat-<ver>-macos-<arch>.dmg` |
| Linux | Linux (oldest distro to support) | `./desktop/packaging/linux/build.sh` | `.deb`, `.rpm`, `.AppImage`, `.flatpak`, `.snap` in `desktop/dist/` |
| Android | any (JDK 21 + Android SDK) | `cd mobile/android; ./gradlew assembleRelease bundleRelease` (signing: see RELEASING) | release `.apk` and `.aab` |
| iOS | a Mac with Xcode | `cd mobile; npx cap open ios`, then build in Xcode | |

Every build self-tests: the desktop binaries run `--selftest`, which processes
an image end to end without opening a window. GitHub Actions builds every
target on every push (see `.github/workflows/`).

See [docs/RELEASING.md](docs/RELEASING.md) for code signing, notarisation and
app-store submission.

## Speed and GPU acceleration

The heaviest step (the blurs behind the visibility mask, the noise fields and
the quality check) runs on the graphics card when there is one, through
WebGPU: Direct3D 12 or Vulkan on Windows, Metal on macOS, Vulkan on Linux,
and the browser's WebGPU on phones (Android 12+, iOS 26+). Without a usable
GPU it runs on all processor cores instead. The JPEG/WebP re-encoding checks
always run on the processor, in parallel.

Measured on a 12 MP image (desktop, GTX 1050 Ti / 4-core i5): **113 s before,
62 s on the processor alone, 39 s with the GPU**. Phones and the browser
gain about 30% with the GPU.

It is on by default and can be switched off under *Performance* (desktop),
*Advanced* (phone), with `--no-gpu` on the command line, or with
`MISTCOAT_GPU=0`. Protection is equally strong either way: same visible
change, same durability.

## Limits

- Not a guarantee (see above). A model-guided attack, which pushes an image's
  embedding toward a chosen wrong concept, would be stronger. It needs PyTorch
  and a CLIP download, so it's planned as an optional add-on engine
  (`desktop/src/mistcoat/engines/`).
- Phones process at up to 8 MP by default (16 MP selectable) to stay within
  mobile memory limits; the app says so when it downscales. Use the desktop
  app for full-resolution scans.
- Phones work in 8 bits, and keep RGB profiles only (see *Colour* above).

## Installing on Linux

Builds are for x86_64 and ARM64 and run on any distribution with glibc 2.35 or
newer (Ubuntu 22.04+, Debian 12+, Fedora 36+, openSUSE Tumbleweed, Mint 21+).

| Package | Install |
|---|---|
| `.deb` (Ubuntu, Debian, Mint, Pop!_OS) | `sudo apt install ./mistcoat_0.1.0_amd64.deb` |
| `.rpm` (Fedora, RHEL, openSUSE) | `sudo dnf install ./mistcoat-0.1.0-1.x86_64.rpm` (openSUSE: `sudo zypper install …`) |
| Flatpak (any distro) | `flatpak install --user Mistcoat-0.1.0-linux-x86_64.flatpak` |
| Snap (Ubuntu and any distro with snapd) | `sudo snap install --dangerous mistcoat_0.1.0_amd64.snap` |
| AppImage (any distro, no install) | `chmod +x Mistcoat-*.AppImage`, then run it |

The Flatpak pulls the Freedesktop 25.08 runtime from Flathub on first install,
and the Snap pulls the GNOME platform snap from the Snap Store. `--dangerous`
is only needed because this file is not from the Store.
The AppImage needs FUSE, which most desktops have; without it, run it with
`--appimage-extract-and-run`.
