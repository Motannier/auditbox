import { guideScenarios } from "./scenarios.js";
import {
  buildPortCheckGuide,
  buildRouteCheckGuide,
  buildDnsCheckGuide,
  buildDiskCheckGuide,
  buildCpuCheckGuide,
  buildMemoryCheckGuide,
  buildServiceStatusGuide,
  buildLogSearchGuide,
  buildRestartRiskGuide,
  buildFirewallCheckGuide,
  buildLoginFailuresGuide,
  buildTimeSyncGuide,
} from "./builders.js";

export function createInitialGuideForm(scenarioId, target, catalog) {
  const scenario = findScenario(scenarioId, catalog);
  if (!scenario) return {};
  return Object.fromEntries(
    scenario.fields.map((field) => [field.key, createInitialFieldValue(field, target)]),
  );
}

export function buildOfflineGuide(scenarioId, form, target, catalog) {
  const scenario = findScenario(scenarioId, catalog);
  if (!scenario) return { commands: [], title: "该设备没有这项速查操作" };
  if (scenario.buildGuide) {
    return scenario.buildGuide(form, target);
  }
  switch (scenarioId) {
    case "route-check":
      return buildRouteCheckGuide(form);
    case "dns-check":
      return buildDnsCheckGuide(form);
    case "disk-check":
      return buildDiskCheckGuide(form);
    case "cpu-check":
      return buildCpuCheckGuide(form);
    case "memory-check":
      return buildMemoryCheckGuide(form);
    case "service-status":
      return buildServiceStatusGuide(form);
    case "log-search":
      return buildLogSearchGuide(form);
    case "restart-risk":
      return buildRestartRiskGuide(form);
    case "firewall-check":
      return buildFirewallCheckGuide(form);
    case "login-failures":
      return buildLoginFailuresGuide(form);
    case "time-sync":
      return buildTimeSyncGuide(form);
    default:
      return buildPortCheckGuide(form, target);
  }
}

export function findScenario(scenarioId, catalog) {
  // Explicit per-device catalogs must never fall back to a Linux operation.
  if (catalog) return catalog.find((scenario) => scenario.id === scenarioId) || null;
  return guideScenarios.find((scenario) => scenario.id === scenarioId) || guideScenarios[0];
}

export function findScenarioBranchId(scenarioId) {
  return findScenario(scenarioId).categoryId;
}

function createInitialFieldValue(field, target) {
  // Only an explicit UI selector has an initial value. Device parameters stay
  // blank; a command form must not borrow the connected host or old values.
  if (field.kind === "select") return field.initialValue ?? "";
  const connected = target?.status === "connected" && Boolean(target?.host);
  if (!connected) return "";
  const networkTarget = ["ssh", "telnet"].includes(String(target?.protocol || "").toLowerCase());
  if (field.key === "host") return networkTarget ? target.host : "";
  if (field.key === "port") return networkTarget ? String(target.port) : "";
  if (field.key === "user" && target.username) return target.username;
  return "";
}

export { guideScenarios } from "./scenarios.js";
export { offlineSkillTree, skillTreeForDeviceProfile } from "./skillTree.js";
export * from "./builders.js";
