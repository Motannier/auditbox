import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  createSecurityChallenge,
  detectGuardian,
  getSecurityPreflight,
  readGuardianCheck,
  refreshSecurityAdmission,
  startGuardianCheck,
  verifySecurityReport,
} from "../services/securityService.js";
import {
  clearSecurityPreflightSession,
  readSecurityPreflightSession,
  writeSecurityPreflightSession,
} from "../lib/securityPreflightStorage.js";
import { useAuth } from "./useAuth.jsx";

const SecurityPreflightContext = createContext(null);
const POLL_INTERVAL_MS = 350;
const CHECK_TIMEOUT_MS = 130_000;
const GUARDIAN_HEALTH_INTERVAL_MS = 5_000;
const DEV_LOGIN_PREVIEW =
  import.meta.env.DEV && new URLSearchParams(window.location.search).get("preview-login") === "1";

function versionTuple(value) {
  const match = String(value || "").trim().match(/^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/);
  return match ? match.slice(1).map(Number) : [-1, -1, -1];
}

function versionAtLeast(current, minimum) {
  const left = versionTuple(current);
  const right = versionTuple(minimum);
  for (let index = 0; index < 3; index += 1) {
    if (left[index] !== right[index]) return left[index] > right[index];
  }
  return true;
}

export function SecurityPreflightProvider({ children }) {
  const { isAuthenticated } = useAuth();
  const [state, setState] = useState(() => {
    if (DEV_LOGIN_PREVIEW) {
      return createTrustedState("passed", { admissionToken: "", config: null });
    }
    return createInitialState(isAuthenticated, readSecurityPreflightSession());
  });
  const runIdRef = useRef(0);

  const run = useCallback(async (knownConfig = null) => {
    const runId = ++runIdRef.current;
    clearSecurityPreflightSession();
    setState((current) => ({
      ...current,
      phase: knownConfig ? "detecting" : "loading",
      results: [],
      error: "",
      admissionToken: "",
    }));
    try {
      const config = knownConfig || (await getSecurityPreflight());
      if (runId !== runIdRef.current) return;
      if (!config.required) {
        writeSecurityPreflightSession({ admissionToken: "", config });
        setState(createTrustedState("passed", { admissionToken: "", config }));
        return;
      }
      setState({ phase: "detecting", config, results: [], error: "", admissionToken: "" });
      try {
        const guardian = await detectGuardian(config.local_url);
        if (!versionAtLeast(guardian.version, config.minimum_guardian_version)) {
          setState({
            phase: "missing",
            config,
            results: [],
            error: `智维卫士版本过旧（当前 ${guardian.version || "未知"}），请安装 ${config.minimum_guardian_version} 或更高版本`,
            admissionToken: "",
          });
          return;
        }
      } catch (error) {
        if (runId !== runIdRef.current) return;
        setState({
          phase: "missing",
          config,
          results: [],
          error: "未检测到正在运行的智维卫士",
          admissionToken: "",
        });
        return;
      }

      const challenge = await createSecurityChallenge();
      const started = await startGuardianCheck(config.local_url, challenge);
      const deadline = Date.now() + CHECK_TIMEOUT_MS;
      let job = null;
      while (Date.now() < deadline) {
        if (runId !== runIdRef.current) return;
        job = await readGuardianCheck(config.local_url, started.check_id);
        setState({
          phase: "checking",
          config,
          results: job.results || [],
          error: job.error || "",
          admissionToken: "",
        });
        if (job.status === "complete") break;
        await delay(POLL_INTERVAL_MS);
      }
      if (!job || job.status !== "complete") {
        throw new Error("本机环境检查超时，请重试");
      }
      if (job.overall !== "pass" || !job.report) {
        setState({
          phase: "failed",
          config,
          results: job.results || [],
          error: job.error || "本机网络环境未达到安全要求",
          admissionToken: "",
        });
        return;
      }

      setState((current) => ({ ...current, phase: "verifying" }));
      const verified = await verifySecurityReport(job.report);
      if (runId !== runIdRef.current) return;
      writeSecurityPreflightSession({
        admissionToken: verified.admission_token,
        expiresIn: verified.expires_in,
        config,
      });
      setState({
        phase: "passed",
        config,
        results: job.results || [],
        error: "",
        admissionToken: verified.admission_token,
      });
    } catch (error) {
      if (runId !== runIdRef.current) return;
      setState((current) => ({
        ...current,
        phase: current.config ? "failed" : "unavailable",
        error: error?.message || "无法完成本机环境检查",
        admissionToken: "",
      }));
    }
  }, []);

  const bootstrapAuthenticatedSession = useCallback(async () => {
    const runId = ++runIdRef.current;
    try {
      const config = await getSecurityPreflight();
      if (runId !== runIdRef.current) return;
      if (!config.required) {
        const trusted = writeSecurityPreflightSession({ admissionToken: "", config });
        setState(createTrustedState("skipped", trusted));
        return;
      }
      const guardian = await detectGuardian(config.local_url);
      if (!versionAtLeast(guardian.version, config.minimum_guardian_version)) {
        throw new Error("智维卫士版本过旧，请重新下载安装");
      }
      if (runId !== runIdRef.current) return;
      const refreshed = await refreshSecurityAdmission();
      if (runId !== runIdRef.current) return;
      const trusted = writeSecurityPreflightSession({
        admissionToken: refreshed.admission_token,
        expiresIn: refreshed.expires_in,
        config,
      });
      setState(createTrustedState("skipped", trusted));
    } catch (error) {
      if (runId !== runIdRef.current) return;
      clearSecurityPreflightSession();
      setState((current) => ({
        phase: "missing",
        config: current.config,
        results: [],
        error: "未检测到正在运行的智维卫士，请重新检查",
        admissionToken: "",
      }));
    }
  }, []);

  useEffect(() => {
    if (DEV_LOGIN_PREVIEW) return undefined;
    const trusted = readSecurityPreflightSession();
    if (trusted) {
      runIdRef.current += 1;
      setState(createTrustedState(isAuthenticated ? "skipped" : "passed", trusted));
      return undefined;
    }
    if (isAuthenticated) {
      bootstrapAuthenticatedSession();
    } else {
      run();
    }
    return () => {
      runIdRef.current += 1;
    };
  }, [bootstrapAuthenticatedSession, isAuthenticated, run]);

  useEffect(() => {
    if (DEV_LOGIN_PREVIEW) return undefined;
    if (
      !["passed", "skipped"].includes(state.phase) ||
      !state.config?.required ||
      !state.config?.local_url
    ) {
      return undefined;
    }

    let stopped = false;
    let checking = false;
    const monitor = window.setInterval(async () => {
      if (checking) return;
      checking = true;
      try {
        await detectGuardian(state.config.local_url);
      } catch {
        if (stopped) return;
        clearSecurityPreflightSession();
        runIdRef.current += 1;
        setState({
          phase: "missing",
          config: state.config,
          results: [],
          error: "智维卫士已断开，本机环境检查结果已失效",
          admissionToken: "",
        });
      } finally {
        checking = false;
      }
    }, GUARDIAN_HEALTH_INTERVAL_MS);

    return () => {
      stopped = true;
      window.clearInterval(monitor);
    };
  }, [state.config, state.phase]);

  const retry = useCallback(() => run(state.config), [run, state.config]);
  const value = useMemo(
    () => ({ ...state, retry }),
    [retry, state],
  );

  return (
    <SecurityPreflightContext.Provider value={value}>
      {children}
    </SecurityPreflightContext.Provider>
  );
}

export function useSecurityPreflight() {
  const context = useContext(SecurityPreflightContext);
  if (!context) throw new Error("useSecurityPreflight must be used within SecurityPreflightProvider");
  return context;
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function createInitialState(isAuthenticated, trusted) {
  if (trusted) return createTrustedState(isAuthenticated ? "skipped" : "passed", trusted);
  if (isAuthenticated) return createTrustedState("skipped", { admissionToken: "", config: null });
  return {
    phase: "loading",
    config: null,
    results: [],
    error: "",
    admissionToken: "",
  };
}

function createTrustedState(phase, trusted) {
  return {
    phase,
    config: trusted.config || null,
    results: [],
    error: "",
    admissionToken: trusted.admissionToken || "",
  };
}
