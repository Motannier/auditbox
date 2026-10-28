import { createBrowserRouter, Navigate } from "react-router-dom";
import { AuthLayout } from "../layouts/AuthLayout.jsx";
import { MainLayout } from "../layouts/MainLayout.jsx";
import { ProtectedLayout } from "../layouts/ProtectedLayout.jsx";
import { ConsolePage } from "../pages/Console/index.jsx";
import { LoginPage } from "../pages/Login/index.jsx";
import { PRODUCT_NAME } from "../constants/branding.js";

function RouteErrorPage() {
  return (
    <main className="login-page">
      <section className="login-shell" aria-label="页面错误">
        <div className="login-brand">
          <div>
            <p className="eyebrow">{PRODUCT_NAME}</p>
            <h1>页面暂时不可用</h1>
          </div>
        </div>
        <p>请重新加载页面；如果问题持续出现，请记录浏览器版本和报错信息后联系管理员。</p>
        <button className="primary-button" type="button" onClick={() => window.location.reload()}>
          重新加载
        </button>
      </section>
    </main>
  );
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Navigate to="/console" replace />,
    errorElement: <RouteErrorPage />,
  },
  {
    element: <AuthLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      {
        path: "/login",
        element: <LoginPage />,
      },
    ],
  },
  {
    element: <ProtectedLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      {
        element: <MainLayout />,
        children: [
          {
            path: "/console",
            element: <ConsolePage />,
          },
        ],
      },
    ],
  },
]);
