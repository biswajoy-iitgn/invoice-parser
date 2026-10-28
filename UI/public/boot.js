// Paints the page in the user's appearance before the app loads, so dark mode never flashes light.
// Same rule as App: the saved choice, else the system setting.
(function () {
  var pref = 'system';
  try { pref = localStorage.getItem('appearance') || 'system'; } catch (e) { /* storage blocked: follow the system */ }
  var dark = pref === 'dark' || (pref === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  var root = document.documentElement;
  root.style.colorScheme = dark ? 'dark' : 'light';
  root.style.backgroundColor = dark ? '#0C0D10' : '#F0F3F7';
})();
