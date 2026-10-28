import { Outlet, useNavigate } from "react-router-dom";
import { TopBar } from "../components/layout/TopBar.jsx";
import { useAuth } from "../hooks/useAuth.jsx";

export function MainLayout() {
  const navigate = useNavigate();
  const { user, isSuperAdmin, logout } = useAuth();

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="app-shell">
      <TopBar user={user} isSuperAdmin={isSuperAdmin} onLogout={handleLogout} />
      <main className="workspace">
        <Outlet />
      </main>
    </div>
  );
}
