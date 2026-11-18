// Resolves the theme before the first paint (no flash). Choice stored per viewer: light, dark or system.
(function () {
  // Same rule as applyTheme() in src/shared/theme/theme.ts: only "light" and "dark" are
  // explicit choices; anything else means "system".
  var choice = "system";
  try {
    var stored = localStorage.getItem("pw-theme");
    if (stored === "light" || stored === "dark") choice = stored;
  } catch (e) {
    /* storage unavailable: follow the system */
  }
  var dark =
    choice === "dark" ||
    (choice === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
})();
