import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { ConsolePortField } from "../../src/components/bookmark/ConsolePortField.jsx";

function Fixture() {
  const [open, setOpen] = useState(true);
  const [value, setValue] = useState(new URLSearchParams(location.search).get("value") || "");
  const [changes, setChanges] = useState(0);
  return <main>
    <button onClick={() => setOpen(!open)}>切换检测窗口</button>
    <output data-testid="changes">{changes}</output>
    {open ? <ConsolePortField value={value} onChange={(next) => {
      setValue(next);
      setChanges((count) => count + 1);
    }} /> : null}
  </main>;
}

createRoot(document.getElementById("root")).render(<Fixture />);
