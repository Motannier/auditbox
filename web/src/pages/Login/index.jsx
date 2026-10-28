import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Eye,
  EyeOff,
  LockKeyhole,
  UserRound,
} from "lucide-react";
import loginIllustration from "../../assets/login-illustration.png";
import { DEFAULT_OPERATOR_USERNAME, PRODUCT_NAME } from "../../constants/branding.js";
import { useAuth } from "../../hooks/useAuth.jsx";
import { ClientDeviceLabel } from "../../components/layout/ClientDeviceLabel.jsx";
import { loginAccount } from "../../services/authService.js";

export function LoginPage() {
  const navigate = useNavigate();
  const { login } = useAuth();
  const [user, setUser] = useState(DEFAULT_OPERATOR_USERNAME);
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    try {
      const account = await loginAccount(user.trim(), password);
      setError("");
      login(account);
      navigate("/console", { replace: true });
    } catch (err) {
      setError(err.message || "用户名或密码错误");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page login-enterprise">
      <section className="login-enterprise-shell" aria-label={`${PRODUCT_NAME}登录`}>
        <aside className="login-enterprise-hero" aria-label={`${PRODUCT_NAME}产品介绍`}>
          <div className="login-enterprise-hero-frame">
            <h1
              className="login-enterprise-product-name"
              style={{
                "--product-name-size": `${Math.min(5.2, 78 / Math.max([...PRODUCT_NAME].length, 1))}cqw`,
                "--product-name-length": Math.max([...PRODUCT_NAME].length, 1),
              }}
            >{PRODUCT_NAME}</h1>
            <img
              className="login-enterprise-hero-image"
              src={loginIllustration}
              alt={`${PRODUCT_NAME}：安全接入、智能审查、风险拦截`}
            />
          </div>
        </aside>

        <section className="login-enterprise-panel">
          <div className="login-enterprise-content">
            <header className="login-enterprise-heading">
              <h2>欢迎使用</h2>
              <p>登录{PRODUCT_NAME}，开始安全运维</p>
            </header>

            <form className="login-enterprise-form" onSubmit={handleSubmit}>
              <div className="login-enterprise-control">
                <label htmlFor="login-username">用户名</label>
                <div className="login-enterprise-field">
                  <UserRound size={22} strokeWidth={1.8} aria-hidden="true" />
                  <input
                    id="login-username"
                    value={user}
                    onChange={(event) => {
                      setUser(event.target.value);
                      setError("");
                    }}
                    autoComplete="username"
                    placeholder="请输入用户名"
                    required
                  />
                </div>
              </div>

              <div className="login-enterprise-control">
                <label htmlFor="login-password">密码</label>
                <div className="login-enterprise-field">
                  <LockKeyhole size={22} strokeWidth={1.8} aria-hidden="true" />
                  <input
                    id="login-password"
                    value={password}
                    onChange={(event) => {
                      setPassword(event.target.value);
                      setError("");
                    }}
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    placeholder="请输入密码"
                    required
                  />
                  <button
                    className="login-password-toggle"
                    type="button"
                    aria-label={showPassword ? "隐藏输入内容" : "显示输入内容"}
                    aria-pressed={showPassword}
                    onClick={() => setShowPassword((current) => !current)}
                  >
                    {showPassword ? <EyeOff size={22} /> : <Eye size={22} />}
                  </button>
                </div>
              </div>

              {error ? (
                <p className="login-enterprise-error" role="alert">
                  {error}
                </p>
              ) : null}

              <button className="login-enterprise-submit" type="submit" disabled={submitting}>
                {submitting ? "正在登录" : "登录"}
              </button>

              <ClientDeviceLabel className="login-enterprise-trust" showIcon />
            </form>
          </div>

          <footer className="login-enterprise-notice">
            仅限授权用户使用，操作行为将被记录
          </footer>
        </section>
      </section>
    </main>
  );
}
