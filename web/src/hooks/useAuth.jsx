import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { clearAuthSession, readAuthSession, writeAuthSession } from "../lib/authStorage.js";
import { synchronizeComputerClock } from "../services/clockService.js";
import { API_BASE_URL } from "../services/http.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [auth, setAuth] = useState(readAuthSession);
  const [clockError, setClockError] = useState("");

  useEffect(() => {
    const refresh = () => { void synchronizeComputerClock(API_BASE_URL, true); };
    const visible = () => { if (document.visibilityState === "visible") refresh(); };
    const synchronized = (event) => {
      setAuth(readAuthSession());
      setClockError(event.detail?.error || "");
    };
    window.addEventListener("online", refresh);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", visible);
    window.addEventListener("auditbox-clock-sync", synchronized);
    return () => {
      window.removeEventListener("online", refresh);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener("auditbox-clock-sync", synchronized);
    };
  }, []);

  const login = useCallback((session) => {
    const nextAuth = {
      username: session.username,
      role: session.role,
      token: session.token,
    };
    writeAuthSession(nextAuth);
    setAuth(nextAuth);
    setClockError(session.clock?.error || "");
  }, []);

  const logout = useCallback(() => {
    clearAuthSession();
    setAuth(null);
    setClockError("");
  }, []);

  const value = useMemo(
    () => ({
      user: auth?.username || "",
      role: auth?.role || "",
      token: auth?.token || "",
      isSuperAdmin: auth?.role === "super_admin",
      isAuthenticated: Boolean(auth?.token),
      login,
      logout,
    }),
    [auth, login, logout],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
      {auth && clockError ? <div className="clock-sync-notice" role="status">{clockError}</div> : null}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
