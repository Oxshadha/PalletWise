// Resolves the theme before the first paint (no flash). Choice stored per viewer: light, dark or system.
(function () {
  var choice = "system";
  try {
    choice = localStorage.getItem("pw-theme") || "system";
  } catch (e) {
    /* storage unavailable: follow the system */
  }
  var dark =
    choice === "dark" ||
    (choice === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
})();
