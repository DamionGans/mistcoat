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
   change them in `packaging/mistcoat.spec`, `mobile/capacitor.config.json`,
   then run `npx cap sync`. They cannot change after a store release.
3. **Bump the version** in `mistcoat/__init__.py`, `packaging/win_version.txt`,
   `mobile/package.json`, and the `VERSION` constant in `mobile/www/sw.js`.

## Windows

`dist\Mistcoat.exe` runs as is, but unsigned executables trigger SmartScreen
("Windows protected your PC" → *More info* → *Run anyway*). To remove that:

- Get an OV or EV code-signing certificate, or use Azure Trusted Signing
  (the cheapest option for individuals).
- Sign: `signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a dist\Mistcoat.exe`
- Optional: publish through the Microsoft Store (MSIX), which signs for you.

## macOS

`packaging/build_mac.sh` ad-hoc signs by default. Such a build opens on other
Macs only via right-click → *Open*. For a normal double-click install:

1. Join the Apple Developer Program (99 USD / year).
2. Create a **Developer ID Application** certificate in Xcode.
3. `xcrun notarytool store-credentials mistcoat-notary --apple-id … --team-id …`
4. Build with:
   `MAC_SIGN_IDENTITY="Developer ID Application: Name (TEAMID)" MAC_NOTARY_PROFILE=mistcoat-notary ./packaging/build_mac.sh`

CI does the same if the `MAC_SIGN_IDENTITY` and `MAC_NOTARY_PROFILE` secrets
are set (the certificate must also be imported into the runner's keychain;
see GitHub's *Installing an Apple certificate on macOS runners* guide).

The CI builds separate Apple Silicon (`arm64`) and Intel (`x86_64`) DMGs.

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

## Web (optional)

`mobile/www` is a complete offline-capable web app. Upload the folder to any
static host (GitHub Pages, Netlify, Cloudflare Pages) and it can be installed
from the browser on Android and iOS ("Add to Home Screen"), with no store
needed. It must be served over HTTPS.
