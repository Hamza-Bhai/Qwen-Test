export const THEME_STORAGE_KEY = "yt-growth-theme";

/** Runs in <head> before first paint so a saved (or system) dark theme never
 *  flashes light. Mirrors applyTheme() in components/theme-toggle.tsx. */
export const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem("${THEME_STORAGE_KEY}");var d=t==="dark"||((t===null||t==="system")&&window.matchMedia("(prefers-color-scheme: dark)").matches);if(d)document.documentElement.classList.add("dark");}catch(e){}})();`;
