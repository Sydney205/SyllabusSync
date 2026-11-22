/* SyllabusSync: service worker registration, install button, offline banner,
   and keyboard handling for the event popovers. No dependencies. */
(function () {
  "use strict";

  // ---- Service worker (makes the app installable and usable offline) ----
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function (err) {
        console.warn("Service worker registration failed:", err);
      });
    });
  }

  // ---- Install button ----
  var installBtn = document.getElementById("install-btn");
  var dialog = document.getElementById("install-dialog");
  var deferredPrompt = null;

  var isStandalone =
    window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  var isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  function showInstall() {
    if (installBtn && !isStandalone) installBtn.hidden = false;
  }

  // Chrome, Edge, Samsung Internet, Android: the browser hands us an install prompt.
  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferredPrompt = event;
    showInstall();
  });

  // iPhone/iPad Safari has no prompt: show the "Add to Home Screen" steps instead.
  if (isIOS) showInstall();

  if (installBtn) {
    installBtn.addEventListener("click", function () {
      if (deferredPrompt) {
        deferredPrompt.prompt();
        deferredPrompt.userChoice.finally(function () {
          deferredPrompt = null;
          installBtn.hidden = true;
        });
      } else if (dialog && typeof dialog.showModal === "function") {
        dialog.showModal();
      }
    });
  }

  window.addEventListener("appinstalled", function () {
    if (installBtn) installBtn.hidden = true;
  });

  // ---- Offline banner ----
  var offlineBar = document.getElementById("offline-bar");
  function updateOnline() {
    if (offlineBar) offlineBar.hidden = navigator.onLine;
  }
  window.addEventListener("online", updateOnline);
  window.addEventListener("offline", updateOnline);
  updateOnline();

  // ---- Event popovers: Escape closes, focus moves into the dialog ----
  function openPopover() {
    var id = decodeURIComponent(location.hash.slice(1));
    if (!id || id === "close") return null;
    var layer = document.getElementById(id);
    return layer && layer.classList.contains("popover-layer") ? layer : null;
  }

  function focusPopover() {
    var layer = openPopover();
    if (!layer) return;
    var target = layer.querySelector(".popover-tools .icon-btn:last-child") || layer.querySelector("a, button");
    if (target) target.focus({ preventScroll: true });
  }

  window.addEventListener("hashchange", focusPopover);
  focusPopover();

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && openPopover()) {
      event.preventDefault();
      location.hash = "close"; // no element has this id, so every popover closes
    }
  });
})();
