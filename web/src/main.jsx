import "./lib/browserCompat.js";
import { createRoot } from "react-dom/client";
import "@xterm/xterm/css/xterm.css";
import "./styles/index.css";
import { App } from "./App.jsx";
import { loadBranding } from "./constants/branding.js";
import { loadDeviceProfiles } from "./lib/deviceProfiles.js";

Promise.all([loadBranding(), loadDeviceProfiles()]).then(() => createRoot(document.getElementById("root")).render(<App />));
