// Pick the design language before first paint: iOS (Human Interface
// Guidelines) or Android (Material 3). In the native apps Capacitor says which
// platform this is; in a browser the user agent does. Anything else (desktop
// browsers) gets the neutral web look.
//
// ?platform=ios|android|web overrides it, for testing.
(function () {
  "use strict";
  var cap = window.Capacitor;
  var name = cap && typeof cap.getPlatform === "function" ? cap.getPlatform() : "web";
  if (name === "web") {
    var ua = navigator.userAgent;
    // iPadOS reports itself as a Mac; touch support gives it away.
    if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) {
      name = "ios";
    } else if (/Android/.test(ua)) {
      name = "android";
    }
  }
  try {
    var forced = new URLSearchParams(location.search).get("platform");
    if (forced === "ios" || forced === "android" || forced === "web") name = forced;
  } catch (e) { /* old WebView without URLSearchParams */ }
  document.documentElement.setAttribute("data-platform", name);

  var SMALL = { a: 1, an: 1, and: 1, as: 1, at: 1, but: 1, by: 1, for: 1, in: 1,
                nor: 1, of: 1, on: 1, or: 1, the: 1, to: 1, up: 1, via: 1 };

  // Button text in the platform's convention: Apple uses title case
  // ("Protect and Save"), Material and the web use sentence case, which is
  // how the strings are written.
  function command(text) {
    if (name !== "ios") return text;
    var words = text.split(" ");
    return words.map(function (w, i) {
      var small = SMALL[w.toLowerCase()] && i > 0 && i < words.length - 1;
      return small || !/^[a-z]/.test(w) ? w : w.charAt(0).toUpperCase() + w.slice(1);
    }).join(" ");
  }

  window.mistcoatPlatform = { name: name, command: command };
})();
