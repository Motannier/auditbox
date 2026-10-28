import "../../src/lib/browserCompat.js";
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import "../../src/styles/index.css";
import "@xterm/xterm/css/xterm.css";
import { TerminalPanel } from "../../src/components/terminal/TerminalPanel.jsx";

function Fixture() {
  const [active, setActive] = useState(0);
  const [targets, setTargets] = useState(["linux", "h3c_comware"].map((profile, index) => ({
    host: "192.0.2.1", port: 22, protocol: "SSH", status: "connecting",
    deviceType: index ? "switch" : "server", deviceProfile: profile, remark: profile,
    websocketUrl: `${location.origin.replace(/^http/, "ws")}/ws/clipboard/${index}`,
  })));
  window.clipboardFixture = {
    switchSession: setActive,
    reconnect: () => setTargets((items) => items.map((target, index) => ({ ...target,
      websocketUrl: `${location.origin.replace(/^http/, "ws")}/ws/clipboard/${index}?reconnect=${Date.now()}` }))),
  };
  return <main style={{ height: "min(700px, 100vh)", width: "100%", position: "relative" }}>
    {targets.map((target, index) => <div key={index} className={`terminal-session ${active === index ? "active" : ""}`}>
      <TerminalPanel target={target} active={active === index}
        onStatusChange={(status) => setTargets((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, status } : item))} />
    </div>)}
  </main>;
}

createRoot(document.getElementById("root")).render(<Fixture />);
