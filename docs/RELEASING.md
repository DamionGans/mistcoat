# Releasing Mistcoat

What is needed to get each build into people's hands without security warnings.
The builds themselves already work; everything below is about signing and
store accounts, which only the publisher can set up.

## Before the first public release

1. **Own the name.** Searches in November 2026 found no app, software or
   trademark called "Mistcoat". Before spending money on store listings,
   register the domain (`mistcoat.app` is what the bundle IDs assume) and
   run a proper trademark search (USPTO / EUIPO / the artist's own country).
2. **Bundle identifiers** are set to `app.mistcoat.desktop` (macOS) and
   `app.mistcoat.mobile` (Android/iOS). If a different domain is used,
   change them in `desktop/packaging/pyinstaller/mistcoat.spec`, `mobile/capacitor.config.json`,
   then run `npx cap sync`. They cannot change after a store release.
3. **Bump the version** in `desktop/src/mistcoat/__init__.py`, `desktop/packaging/windows/version_info.txt`,
   `mobile/package.json`, `MARKETING_VERSION` in
   `mobile/ios/App/App.xcodeproj/project.pbxproj`, `versionName` (and
   `versionCode`) in `mobile/android/app/build.gradle`, the `VERSION` constant in `mobile/www/sw.js`, and
   add a `<release>` line to `desktop/packaging/linux/app.mistcoat.Mistcoat.metainfo.xml`.
4. **Pick a licence.** There is no LICENSE file yet, so the Linux packages
   declare the app proprietary (`license:` in `desktop/packaging/linux/nfpm.yaml`,
   `project_license` in the metainfo). Update both once one is chosen.

## Windows

`desktop\dist\Mistcoat.exe` runs as is, but unsigned executables trigger SmartScreen
("Windows protected your PC" → *More info* → *Run anyway*). To remove that:

- Get an OV or EV code-signing certificate, or use Azure Trusted Signing
  (the cheapest option for individuals).
- Sign: `signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a desktop\dist\Mistcoat.exe`
- Optional: publish through the Microsoft Store (MSIX), which signs for you.

## macOS

`desktop/packaging/macos/build.sh` ad-hoc signs by default. Such a build opens on other
Macs only via right-click → *Open*. For a normal double-click install:

1. Join the Apple Developer Program (99 USD / year).
2. Create a **Developer ID Application** certificate in Xcode.
3. `xcrun notarytool store-credentials mistcoat-notary --apple-id … --team-id …`
4. Build with:
   `MAC_SIGN_IDENTITY="Developer ID Application: Name (TEAMID)" MAC_NOTARY_PROFILE=mistcoat-notary ./desktop/packaging/macos/build.sh`

CI does the same if the `MAC_SIGN_IDENTITY` and `MAC_NOTARY_PROFILE` secrets
are set (the certificate must also be imported into the runner's keychain;
see GitHub's *Installing an Apple certificate on macOS runners* guide).

The CI builds separate Apple Silicon (`arm64`) and Intel (`x86_64`) DMGs.

## Linux

`desktop/packaging/linux/build.sh` produces all four formats. CI builds them for
x86_64 and ARM64 and install-tests each one: the .deb on Ubuntu, the .rpm on
Fedora and openSUSE, the Flatpak and the AppImage directly. None need signing
to install. For publishing:

- **.deb / .rpm as downloads:** fine as they are. For a proper apt/dnf
  repository, sign the repository metadata with a GPG key (`aptly`, or a
  hosted service such as Cloudsmith or packagecloud).
- **Flathub:** the `.flatpak` bundle repackages the PyInstaller build, which
  Flathub does not accept; Flathub builds from source. Submitting needs a
  manifest that builds the Python dependencies (`flatpak-pip-generator`)
  against `org.kde.Platform`, which provides Qt, and a pull request to
  `flathub/flathub`. Flathub verifies the app ID's domain, so
  `app.mistcoat.Mistcoat` requires owning `mistcoat.app`.
- **AppImage:** works as a download; optionally list it on AppImageHub.
- **Snap** is not built. It can be added with a `snapcraft.yaml` if wanted.

## Android

- **Sideloading / testing:** the debug APK from CI or
  `mobile/android/app/build/outputs/apk/debug/` installs directly
  (Settings → allow installs from this source).
- **Google Play:**
  1. Create a Play Console developer account (one-time fee).
  2. Generate an upload key: `keytool -genkeypair -v -keystore mistcoat-upload.jks -alias upload -keyalg RSA -keysize 4096 -validity 10000`
     Keep it safe and out of git.
  3. Add a `signingConfigs.release` block to `mobile/android/app/build.gradle`
     that reads the keystore path and passwords from environment variables.
  4. `cd mobile/android && ./gradlew bundleRelease` → upload the `.aab`.
  5. Play requires a privacy policy URL. Mistcoat collects nothing; the policy
     can say exactly that.

## iOS

Can only be built on a Mac with Xcode.

1. Apple Developer Program (the same membership as macOS).
2. `cd mobile && npm ci && npx cap sync ios && npx cap open ios`
3. In Xcode: select the *App* target → *Signing & Capabilities* → pick the team.
4. Run on a device or simulator to test; *Product → Archive* → *Distribute App*
   for TestFlight / App Store.
5. App Store Connect needs a privacy policy URL and the "Data Not Collected"
   privacy label, which is accurate: images never leave the device.

CI compiles the iOS app for the simulator on every push (unsigned), so build
breakage is caught even without a Mac to hand.

### Testing on an iPhone without a Mac (sideloading)

Every CI run also produces `Mistcoat-<ver>-ios-unsigned.ipa` (artifact
*Mistcoat-ios-unsigned-ipa*). iOS refuses unsigned apps, so a sideloading
tool signs it with your own Apple ID on the way onto the phone:

1. On Windows or a Mac, install **Sideloadly** (or **AltStore**).
2. Connect the iPhone by cable and trust the computer.
3. Drop the `.ipa` into Sideloadly, enter your Apple ID, and start.
4. On the phone: *Settings → General → VPN & Device Management*, trust your
   Apple ID's developer profile. On iOS 16+ also enable *Settings → Privacy
   & Security → Developer Mode* and restart when asked.

With a free Apple ID the app stops opening after **7 days** and must be
sideloaded again (AltStore can refresh it automatically over Wi-Fi), and at
most three sideloaded apps can be active at a time. A paid developer account
extends this to a year. This is for testing only; anyone else installing it
needs their own Apple ID, or a TestFlight build.

## Web (optional)

`mobile/www` is a complete offline-capable web app. Upload the folder to any
static host (GitHub Pages, Netlify, Cloudflare Pages) and it can be installed
from the browser on Android and iOS ("Add to Home Screen"), with no store
needed. It must be served over HTTPS.
