import {
  AlertTriangle,
  Check,
  Download,
  LoaderCircle,
  RotateCw,
  ShieldCheck,
  ShieldX,
} from "lucide-react";
import { useSecurityPreflight } from "../../hooks/useSecurityPreflight.jsx";
import { PRODUCT_NAME } from "../../constants/branding.js";

const PHASE_COPY = {
  loading: ["正在准备安全检查", () => `正在读取${PRODUCT_NAME}的安全策略`],
  detecting: ["正在连接智维卫士", "确认本机检测服务已经启动"],
  checking: ["正在检查本机网络环境", "请勿切换网络，检查结果会实时更新"],
  verifying: ["正在验证检查结果", () => `${PRODUCT_NAME}正在核验签名和检测时效`],
  failed: ["本机网络环境未通过", "处理下列问题后重新检查"],
  unavailable: ["暂时无法开始检查", "请确认 Type-C 管理线已连接后重试"],
};

const CHECK_STEPS = [
  { id: "usb_link", label: "USB 管理链路" },
  { id: "firewall", label: "防火墙" },
  { id: "external_access", label: "违规外联" },
];

export function SecurityPreflightGate({ children }) {
  const preflight = useSecurityPreflight();
  if (preflight.phase === "passed" || preflight.phase === "skipped") return children;
  if (preflight.phase === "missing") return <GuardianMissing preflight={preflight} />;

  const [title, subtitle] = PHASE_COPY[preflight.phase] || PHASE_COPY.loading;
  const failed = preflight.phase === "failed" || preflight.phase === "unavailable";
  return (
    <main className="security-gate" aria-live="polite">
      <section className="security-card security-check-card" aria-label="本机网络环境检查">
        <header className="security-heading">
          <div className={`security-shield ${failed ? "is-failed" : ""}`}>
            {failed ? <ShieldX size={30} /> : <ShieldCheck size={30} />}
          </div>
          <div>
            <p className="eyebrow">智维卫士</p>
            <h1>{title}</h1>
            <p>{typeof subtitle === "function" ? subtitle() : subtitle}</p>
          </div>
        </header>

        <CheckProgress results={preflight.results} phase={preflight.phase} />

        {preflight.error ? (
          <div className="security-error" role="alert">
            <AlertTriangle size={17} />
            <span>{preflight.error}</span>
          </div>
        ) : null}
        {failed ? (
          <button className="primary-button security-retry" type="button" onClick={preflight.retry}>
            <RotateCw size={17} />
            重新检查
          </button>
        ) : (
          <div className="security-running">
            <LoaderCircle className="spin" size={18} />
            检查进行中
          </div>
        )}
      </section>
    </main>
  );
}

function GuardianMissing({ preflight }) {
  const downloadAvailable = preflight.config?.download_available;
  const outdated = preflight.error?.includes("版本过旧");
  return (
    <main className="security-gate" aria-live="polite">
      <section className="security-card" aria-label="安装智维卫士">
        <header className="security-heading">
          <div className="security-shield is-failed"><ShieldX size={30} /></div>
          <div>
            <p className="eyebrow">智维卫士</p>
            <h1>{outdated ? "需要更新本地智维卫士" : "需要启动本地智维卫士"}</h1>
            <p>{outdated ? preflight.error : "首次使用请在 Windows 10 或 Windows 11 安装，安装后会随系统自动启动。"}</p>
          </div>
        </header>
        <ol className="security-steps">
          <li><span>1</span><div><strong>下载并安装</strong><p>按安装向导完成安装，无需修改选项。</p></div></li>
          <li><span>2</span><div><strong>确认任务栏图标</strong><p>任务栏右下角出现盾牌图标即表示卫士正在运行；浏览器询问本地网络访问权限时请选择允许。</p></div></li>
          <li><span>3</span><div><strong>重新检测</strong><p>返回本页点击“我已启动，重新检测”。</p></div></li>
        </ol>
        <div className="security-actions">
          {downloadAvailable ? (
            <a className="primary-button" href={preflight.config.download_url}>
              <Download size={17} />下载 Windows 安装包
            </a>
          ) : (
            <p className="security-package-note">安装包尚未放入{PRODUCT_NAME}，请联系管理员。</p>
          )}
          <button className="secondary-button" type="button" onClick={preflight.retry}>
            <RotateCw size={17} />我已启动，重新检测
          </button>
        </div>
      </section>
    </main>
  );
}

function CheckProgress({ results, phase }) {
  const resultById = new Map(results.map((result) => [result.id, result]));
  const combineLegacy = (id, legacyIds, label) => {
    if (resultById.has(id)) return;
    const legacy = legacyIds.map((legacyId) => resultById.get(legacyId)).filter(Boolean);
    if (!legacy.length) return;
    const status = legacy.some((item) => item.status === "fail")
      ? "fail"
      : legacy.every((item) => item.status === "pass")
        ? "pass"
        : legacy.some((item) => item.status === "running")
          ? "running"
          : "pending";
    const failedMessage = legacy.find((item) => item.status === "fail")?.message;
    resultById.set(id, {
      id,
      label,
      status,
      message: failedMessage || (status === "pass" ? "检查通过" : "正在检查"),
    });
  };
  combineLegacy("usb_link", ["usb_link", "management_network"], "USB 管理链路");
  if (resultById.has("management_network") && resultById.has("usb_link")) {
    const usb = resultById.get("usb_link");
    const network = resultById.get("management_network");
    if (network.status === "fail") resultById.set("usb_link", { ...usb, status: "fail", message: network.message });
    else if (usb.status === "pass" && network.status !== "pass") {
      resultById.set("usb_link", { ...usb, status: network.status, message: network.message });
    }
  }
  combineLegacy(
    "external_access",
    ["public_connections", "public_probe"],
    "违规外联",
  );
  const steps = CHECK_STEPS.map((step) => ({
    ...step,
    ...(resultById.get(step.id) || { status: "pending", message: "等待检查" }),
  }));
  const completed = steps.filter((step) => ["pass", "fail", "warning"].includes(step.status)).length;
  const activeIndex = steps.findIndex((step) => step.status === "running");
  const lastCompletedIndex = steps.reduce(
    (last, step, index) => (["pass", "fail", "warning"].includes(step.status) ? index : last),
    -1,
  );
  const visualIndex = activeIndex >= 0 ? activeIndex : lastCompletedIndex;
  const progress = visualIndex < 0 ? 0 : (visualIndex / (steps.length - 1)) * 100;
  const failed = steps.filter((step) => step.status === "fail");

  return (
    <section className={`security-progress ${failed.length ? "has-failure" : ""}`} aria-label="安全检查进度">
      <div className="security-progress-summary">
        <strong>检测进度</strong>
        <span>{completed} / {steps.length}</span>
      </div>
      <div className="security-progress-viewport">
        <div
          className="security-progress-rail"
          role="progressbar"
          aria-valuemin="0"
          aria-valuemax={steps.length}
          aria-valuenow={completed}
          aria-label={`已完成 ${completed} 项，共 ${steps.length} 项`}
          style={{ "--security-progress": `${progress}%` }}
        >
          <div className="security-progress-track">
            <span className="security-progress-fill" />
          </div>
          <ol className="security-progress-steps">
            {steps.map((step, index) => (
              <li key={step.id} className={`is-${step.status}`}>
                <span className="security-progress-node">
                  {step.status === "running" ? (
                    <LoaderCircle className="spin" size={15} />
                  ) : step.status === "pass" ? (
                    <Check size={15} />
                  ) : step.status === "fail" ? (
                    <AlertTriangle size={14} />
                  ) : (
                    index + 1
                )}
                </span>
              </li>
            ))}
          </ol>
        </div>
      </div>
      <ul className="security-progress-details" aria-label="检查项目状态">
        {steps.map((step) => (
          <li key={step.id} className={`is-${step.status}`}>
            <span className="security-detail-icon">
              {step.status === "running" ? (
                <LoaderCircle className="spin" size={15} />
              ) : step.status === "pass" ? (
                <Check size={15} />
              ) : step.status === "fail" ? (
                <AlertTriangle size={14} />
              ) : (
                <span />
              )}
            </span>
            <div>
              <strong>{step.label}</strong>
              <p>{phase === "verifying" && step.status === "pass" ? "检查通过，等待签名核验" : step.message}</p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
