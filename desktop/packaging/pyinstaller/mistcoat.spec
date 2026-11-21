# PyInstaller spec - one file for both desktop targets.
#
# Run from desktop/ (the build scripts in packaging/<os>/ do this):
#
#   Windows:  pyinstaller packaging/pyinstaller/mistcoat.spec -> dist/Mistcoat.exe
#             (+ dist/mistcoat-cli.exe, the console build of the command line)
#   macOS:    pyinstaller packaging/pyinstaller/mistcoat.spec -> dist/Mistcoat.app
#   Linux:    pyinstaller packaging/pyinstaller/mistcoat.spec -> dist/Mistcoat/ (a
#             folder; packaging/linux/build.sh wraps it as .deb/.rpm/AppImage/Flatpak)
#
# Must be run on the target OS; PyInstaller does not cross-compile.
import sys
from pathlib import Path

HERE = Path(SPECPATH)                 # desktop/packaging/pyinstaller
DESKTOP = HERE.parents[1]             # desktop/
SRC = DESKTOP / "src"
PACKAGING = DESKTOP / "packaging"
sys.path.insert(0, str(SRC))
from mistcoat import __version__  # noqa: E402

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

# Qt ships a lot we never touch. Dropping it roughly halves the download.
# (QtNetwork stays: its local sockets hand files to an already-open window.)
EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "tkinter", "unittest", "pytest", "torch",
]

a = Analysis(
    [str(HERE / "launch.py")],
    pathex=[str(SRC)],
    datas=[(str(SRC / "mistcoat" / "resources"), "mistcoat/resources")],
    hiddenimports=["PIL.ImageCms"],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = str(PACKAGING / "macos" / "mistcoat.icns" if IS_MAC
          else PACKAGING / "windows" / "mistcoat.ico")

if IS_MAC:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Mistcoat",
              console=False, icon=icon, target_arch=None)
    coll = COLLECT(exe, a.binaries, a.datas, name="Mistcoat")
    app = BUNDLE(
        coll,
        name="Mistcoat.app",
        icon=icon,
        bundle_identifier="app.mistcoat.desktop",
        version=__version__,
        info_plist={
            "CFBundleName": "Mistcoat",
            "CFBundleDisplayName": "Mistcoat",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSHumanReadableCopyright": "Mistcoat",
            # Let people drop images on the Dock icon.
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Image",
                "CFBundleTypeRole": "Viewer",
                "LSItemContentTypes": ["public.image"],
            }],
        },
    )
elif IS_LINUX:
    # A folder, not a onefile: package managers install it once instead of the
    # app unpacking ~150 MB to /tmp on every launch.
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Mistcoat",
              console=False, upx=False, strip=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="Mistcoat", upx=False)
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="Mistcoat",
        console=False,
        icon=icon,
        upx=False,          # UPX-packed Qt binaries trip antivirus heuristics
        version=str(PACKAGING / "windows" / "version_info.txt") if IS_WIN else None,
    )

    # Windows also gets a console build of the command line (no Qt): it is
    # what terminals and scripts expect, while the windowed Mistcoat.exe above
    # runs the same commands with no window at all. See docs/CLI.md.
    if IS_WIN:
        cli_a = Analysis(
            [str(HERE / "launch_cli.py")],
            pathex=[str(SRC)],
            hiddenimports=["PIL.ImageCms"],
            excludes=EXCLUDES + ["PySide6", "shiboken6"],
            noarchive=False,
        )
        cli_pyz = PYZ(cli_a.pure)
        cli_exe = EXE(
            cli_pyz, cli_a.scripts, cli_a.binaries, cli_a.datas, [],
            name="mistcoat-cli",
            console=True,
            icon=icon,
            upx=False,
            version=str(PACKAGING / "windows" / "version_info.txt"),
        )
