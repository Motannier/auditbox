import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { ArrowRight, Clock3, LockKeyhole } from "lucide-react";

export function SessionExpiredDialog({ onRelogin }) {
  const dialogRef = useRef(null);
  const buttonRef = useRef(null);

  useEffect(() => {
    const root = document.getElementById("root");
    const previousHidden = root?.getAttribute("aria-hidden");
    const previousInert = root?.getAttribute("inert");
    const focusButton = () => buttonRef.current?.focus();
    const keepFocus = (event) => {
      if (!dialogRef.current?.contains(event.target)) focusButton();
    };
    const guardKeys = (event) => {
      const outside = !dialogRef.current?.contains(event.target);
      if (outside || event.key === "Tab" || event.key === "Escape") {
        event.preventDefault();
        focusButton();
      }
      // Also guard older browsers without native inert support.
      event.stopPropagation();
    };
    focusButton();
    root?.setAttribute("aria-hidden", "true");
    root?.setAttribute("inert", "");
    document.addEventListener("focusin", keepFocus, true);
    document.addEventListener("keydown", guardKeys, true);
    return () => {
      document.removeEventListener("focusin", keepFocus, true);
      document.removeEventListener("keydown", guardKeys, true);
      if (previousHidden === null) root?.removeAttribute("aria-hidden");
      else if (previousHidden !== undefined) root?.setAttribute("aria-hidden", previousHidden);
      if (previousInert === null) root?.removeAttribute("inert");
      else if (previousInert !== undefined) root?.setAttribute("inert", previousInert);
    };
  }, []);

  return createPortal(
    <div className="session-expired-backdrop">
      <section className="session-expired-dialog" ref={dialogRef}
        role="alertdialog" aria-modal="true" aria-labelledby="session-expired-title"
        aria-describedby="session-expired-description">
        <div className="session-expired-icon" aria-hidden="true">
          <LockKeyhole size={46} strokeWidth={1.8} />
          <Clock3 className="session-expired-clock" size={26} strokeWidth={2} />
        </div>
        <h2 id="session-expired-title">登录已失效</h2>
        <p id="session-expired-description">当前登录已过期，请重新登录后继续使用。</p>
        <button className="session-expired-login" type="button" ref={buttonRef} onClick={onRelogin}>
          重新登录 <ArrowRight size={20} aria-hidden="true" />
        </button>
      </section>
    </div>, document.body,
  );
}
