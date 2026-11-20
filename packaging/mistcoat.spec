# PyInstaller spec - one file for both desktop targets.
#
#   Windows:  pyinstaller packaging/mistcoat.spec   -> dist/Mistcoat.exe
#   macOS:    pyinstaller packaging/mistcoat.spec   -> dist/Mistcoat.app
#   Linux:    pyinstaller packaging/mistcoat.spec   -> dist/Mistcoat/ (a folder;
#             packaging/linux/build_linux.sh wraps it as .deb/.rpm/AppImage/Flatpak)
#
# Must be run on the target OS; PyInstaller does not cross-compile.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from mistcoat import __version__  # noqa: E402

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

# Qt ships a lot we never touch. Dropping it roughly halves the download.
EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtNetwork",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "tkinter", "unittest", "pytest", "torch",
]

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "assets" / "icon.png"), "assets")],
    hiddenimports=["PIL.ImageCms"],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = str(ROOT / "assets" / ("icon.icns" if IS_MAC else "icon.ico"))

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
        version=str(ROOT / "packaging" / "win_version.txt") if IS_WIN else None,
    )
