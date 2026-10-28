import { Activity, Cable, ClipboardList, Network, Settings, ShieldAlert, Terminal } from "lucide-react";
import { DEVICE_TYPES, deviceProfileDisplayName, deviceTypeDisplayName } from "../deviceProfiles.js";

const SLOT = /<[^<>\r\n]{1,80}>|\{[a-zA-Z][a-zA-Z0-9_-]{0,79}\}/g;
const ICONS = { system: Activity, access: Network, interface: Cable, service: Network,
  routing: Network, security: ShieldAlert, fault: ShieldAlert, configuration: ClipboardList, maintenance: Settings };
export const GUIDE_VARIANT_FIELD = "_guide_variant";
const RISK_LABELS = { read_only: "只读查询", ordinary_change: "普通修改 · 执行需确认",
  dangerous_change: "危险修改 · 执行需确认" };
RISK_LABELS.pending = "风险待核验 · 执行需确认";

function parameterField(parameter) {
  return { ...parameter, kind: parameter.validation === "enum" ? "select" : "text",
    options: parameter.validation === "enum"
      ? [{ value: "", label: `请选择${parameter.label}` }, ...parameter.options] : undefined };
}

function validParameter(value, parameter) {
  if (!/^[a-zA-Z0-9_./:@,+%=-]{1,256}$/.test(value)) return false;
  if (parameter.literals?.includes(value)) return true;
  if (parameter.validation === "enum") return parameter.options.some((option) => option.value === value);
  if (parameter.validation === "integer") {
    if (!/^\d{1,10}$/.test(value)) return false;
    const n = Number(value);
    return (parameter.minimum == null || n >= parameter.minimum) && (parameter.maximum == null || n <= parameter.maximum);
  }
  if (parameter.validation === "ipv4") return /^(?:\d{1,3}\.){3}\d{1,3}$/.test(value)
    && value.split(".").every((part) => Number(part) <= 255);
  if (parameter.validation === "position") return parameter.formats.some(({ prefix, separators }) => {
    if (!value.startsWith(prefix)) return false;
    const body = value.slice(prefix.length), parts = body.split(/[/:]/), actualSeparators = body.match(/[/:]/g) || [];
    return parts.length === separators.length + 1 && parts.every((part) => /^\d{1,10}$/.test(part))
      && separators.every((separator, index) => actualSeparators[index] === separator);
  });
  return true;
}

// The quick guide is a function picker, not a firmware/grammar editor. Keep
// all documented forms as data, but choose the normal form for the connected
// execution profile internally. Never borrow an OLT command for an ONU.
export function selectPackageGuideVariant(scenario, target) {
  if (!scenario?.runtime || !scenario.variants?.length) return null;
  const matching = scenario.variants.filter((v) => v.deviceProfile === target?.deviceProfile
    && v.deviceType === target?.deviceType);
  const olt = scenario.variants.filter((v) => v.deviceType === "olt");
  const candidates = matching.length ? matching : olt.length ? olt : scenario.variants;
  const rank = (v) => [
    Number(v.deviceType === "olt" && ((!/(^|\/)\s*CLI\s*(\/|$)/.test(v.interface || "") && /专家|恢复专用/.test(v.interface || ""))
      || v.views?.length > 0 && v.views.every((view) => /\bsu\b|诊断|diagnos|debug/i.test(view)))),
    Number(v.documentation_only),
    -(v.models?.length || 0),
    v.parameters.length,
    (v.template || v.syntax || "").split(/\s+/).length,
  ];
  return [...candidates].sort((a, b) => {
    const left = rank(a), right = rank(b);
    for (let i = 0; i < left.length; i++) if (left[i] !== right[i]) return left[i] - right[i];
    return a.id.localeCompare(b.id);
  })[0];
}

const PON_BRANDS = [
  ["fiberhome", "烽火", "AN6000 / AN5516-06 · EMS/TL1"],
  ["huawei", "华为", "MA5800 / MA5600T / MA5680T · MA5621 / ONU V500R022"],
  ["raisecom", "瑞斯康达", "ISCOM5504 / 5508 / 6800 / 6820 / 6860 · HT803G / ISCOM5104G-GP"],
  ["zte", "中兴", "C220 / C300 / C320 / C69E · F803 / F809"],
];

export function packageGuideCounts(data) {
  if (data?.schema !== "auditbox-command-guide-counts/1" || !Array.isArray(data.profiles)) {
    throw new Error("操作数量资料不完整，请重新加载");
  }
  const counts = {};
  for (const row of data.profiles) {
    if (!row || typeof row.profile_id !== "string" || !/^[a-z][a-z0-9_-]{0,63}$/.test(row.profile_id)
        || Object.hasOwn(counts, row.profile_id)
        || !(Number.isSafeInteger(row.count) && row.count >= 0 || row.count === null && typeof row.error === "string")) {
      throw new Error("操作数量资料不完整，请重新加载");
    }
    counts[row.profile_id] = { count: row.count, error: row.error || "" };
  }
  for (const brand of data.brands || []) {
    const definition = PON_BRANDS.find(([vendor]) => brand.id === `pon-${vendor}`);
    if (!definition || !Array.isArray(brand.profile_ids) || !brand.profile_ids.length
        || new Set(brand.profile_ids).size !== brand.profile_ids.length
        || brand.profile_ids.some((id) => ![`${definition[0]}_olt`, `${definition[0]}_onu`].includes(id) || !counts[id])
        || !(Number.isSafeInteger(brand.count) && brand.count >= 0 || brand.count === null)
        || typeof brand.model_summary !== "string") throw new Error("品牌操作数量资料不完整");
    counts[brand.id] = { count: brand.count, modelSummary: brand.model_summary };
  }
  return counts;
}

// Only fill a single documented CLI template. No Enter, guessed parameters,
// shell fragments or executable code from a package are accepted here.
export function resolvePackageGuideScenario(scenario, form = {}) {
  if (!scenario?.runtime || !scenario.variants) return scenario;
  const variantId = form[GUIDE_VARIANT_FIELD] ?? selectPackageGuideVariant(scenario)?.id;
  const variant = scenario.variants.find((entry) => entry.id === variantId);
  if (!variant) return null; // Never fall through to a different command form.
  return {
    ...scenario, ...variant, id: scenario.id,
    riskLabel: RISK_LABELS[variant.risk], danger: variant.risk !== "read_only",
    fields: variant.parameters.map(parameterField),
  };
}

export function buildPackageGuide(scenario, form = {}) {
  scenario = resolvePackageGuideScenario(scenario, form);
  if (!scenario) return { title: "请选择有效的命令形式", commands: [] };
  if (scenario.documentation_only) return { title: "手册参考内容；未编译为可下发命令", commands: [] };
  let index = 0;
  let error = "";
  const command = scenario.template.replace(SLOT, (slot) => {
    const parameter = scenario.parameters[index++];
    if (!parameter || parameter.slot !== slot) { error = "命令参数资料不完整"; return slot; }
    const raw = String(form[parameter.key] ?? "");
    const value = raw.trim();
    if (!value) error ||= `请填写${parameter.label}`;
    else if (raw !== value || !validParameter(value, parameter)) {
      error ||= `${parameter.label}格式不正确，不能包含空格或命令控制字符`;
    }
    return value;
  });
  if (index !== scenario.parameters.length || /[<>{}\r\n;&|`$\\[\]]/.test(command)) {
    error ||= "该命令模板暂不能安全填入";
  }
  return { title: error || scenario.title, commands: error ? [] : [command] };
}

export function packageGuideCatalog(data, profile) {
  if (data?.schema !== "auditbox-command-guide/1" || data.profile_id !== profile || data.fill_only !== true
      || !Array.isArray(data.categories)) throw new Error("设备速查资料不匹配，请重新加载");
  if (!data.available) return { scenarios: [], groups: [], skillTree: [], message: data.message || "该品牌暂无 CLI 速查资料" };
  const scenarios = [];
  const groups = data.categories.map((category) => {
    if (!Array.isArray(category.scenarios)) throw new Error("设备速查分类资料不完整");
    for (const entry of category.scenarios) {
      const rawForms = entry.variants ?? [entry];
      if (entry.fill_only !== true || typeof entry.id !== "string" || typeof entry.title !== "string"
          || !Array.isArray(rawForms) || !rawForms.length || rawForms.some((variant) =>
        !variant || !Object.hasOwn(RISK_LABELS, variant.risk) || variant.fill_only !== true || typeof variant.template !== "string"
        || variant.documentation_only !== true && !variant.template.trim()
        || variant.documentation_only === true && (variant.template !== "" || variant.parameters?.length !== 0)
        || !Array.isArray(variant.parameters) || variant.parameters.some((parameter) => !parameter
          || typeof parameter.key !== "string" || parameter.key === GUIDE_VARIANT_FIELD
          || typeof parameter.label !== "string" || typeof parameter.slot !== "string"
          || parameter.validation != null && !["token", "integer", "ipv4", "enum", "position"].includes(parameter.validation)
          || [parameter.minimum, parameter.maximum].some((bound) => bound != null && !Number.isSafeInteger(bound))
          || parameter.minimum != null && parameter.maximum != null && parameter.minimum > parameter.maximum
          || parameter.literals != null && (!Array.isArray(parameter.literals) || parameter.literals.some((v) =>
            typeof v !== "string" || !/^[a-zA-Z0-9_./:@,+%=-]{1,256}$/.test(v)))
          || parameter.validation === "enum" && (!Array.isArray(parameter.options) || !parameter.options.length || parameter.options.length > 100
            || parameter.options.some((option) => !option || typeof option.label !== "string"
              || typeof option.value !== "string" || !/^[a-zA-Z0-9_./:@,+%=-]{1,256}$/.test(option.value)))
          || parameter.validation === "position" && (!Array.isArray(parameter.formats) || !parameter.formats.length
            || parameter.formats.length > 100 || parameter.formats.some((fmt) => !fmt || typeof fmt.prefix !== "string"
              || !/^[a-zA-Z0-9_.-]*$/.test(fmt.prefix) || !Array.isArray(fmt.separators) || fmt.separators.length > 10
              || fmt.separators.some((s) => s !== "/" && s !== ":"))))
        || new Set(variant.parameters.map((parameter) => parameter.key)).size !== variant.parameters.length
        || (variant.views != null && (!Array.isArray(variant.views) || variant.views.some((view) => typeof view !== "string")))
        || (variant.models != null && (!Array.isArray(variant.models) || variant.models.some((model) => typeof model !== "string")))
        || (variant.evidence != null && (!Array.isArray(variant.evidence) || variant.evidence.some((item) => !item
          || typeof item.source_id !== "string" || !Array.isArray(item.pages)
          || item.pages.some((page) => !Number.isInteger(page) || page < 1))))) || (entry.variants && (rawForms.some((variant) =>
        typeof variant.id !== "string" || !variant.id) || new Set(rawForms.map((variant) => variant.id)).size !== rawForms.length))) {
        throw new Error("设备速查命令资料不完整");
      }
      // Document data cannot override ownership, the form renderer or the
      // dispatch gate when a variant is selected. Keep only the DTO fields.
      const forms = rawForms.map((variant) => ({
        id: variant.id, template: variant.template, purpose: variant.purpose, parameters: variant.parameters,
        risk: variant.risk, risk_reason: variant.risk_reason, views: variant.views, models: variant.models,
        evidence: variant.evidence, command_ids: variant.command_ids, fill_only: true,
        documentation_only: variant.documentation_only === true, syntax: typeof variant.syntax === "string" ? variant.syntax : "",
        syntaxes: Array.isArray(variant.syntaxes) && variant.syntaxes.every((syntax) => typeof syntax === "string") ? variant.syntaxes : [],
        steps: Array.isArray(variant.steps) && variant.steps.every((step) => typeof step === "string") ? variant.steps : [],
        interface: typeof variant.interface === "string" ? variant.interface : "CLI",
        deviceProfile: profile, deviceType: data.device_type,
      }));
      const scenario = {
        ...entry, ...forms[0], id: entry.id, title: entry.title, summary: entry.summary,
        variants: entry.variants ? forms : undefined,
        variantField: null, categoryId: category.id, icon: ICONS[category.key] || Terminal,
        runtime: true, deviceProfile: profile, deviceType: data.device_type,
        riskLabel: RISK_LABELS[forms[0].risk], danger: forms[0].risk !== "read_only",
        fields: forms[0].parameters.map(parameterField),
      };
      scenario.buildGuide = (form) => buildPackageGuide(scenario, form);
      scenarios.push(scenario);
    }
    return { id: category.id, label: category.label, summary: category.summary, icon: ICONS[category.key] || Terminal };
  });
  const skillTree = scenarios.length ? [{
    id: `package-guide-${profile}`, label: `${deviceTypeDisplayName(data.device_type)}运维`,
    summary: "按常用操作查找命令，填写参数后填入终端", icon: Network,
    branches: [{ id: `package-guide-brand-${profile}`, label: data.label,
      summary: "当前品牌已安装的指令速查", icon: Terminal, groups }],
  }] : [];
  return { scenarios, groups, skillTree, message: data.message || "", modelSummary: data.model_summary || "" };
}

export function mergedPonCatalog(vendor, states, profiles) {
  const catalogs = profiles.map((id) => states[id]?.catalog).filter(Boolean);
  const operations = new Map(), groups = new Map();
  for (const catalog of catalogs) {
    for (const group of catalog.groups) {
      const key = group.id.split("-").at(-1);
      groups.set(key, { ...group, id: `guide-pon-${vendor}-${key}` });
    }
    for (const scenario of catalog.scenarios) {
      const group = scenario.categoryId.split("-").at(-1);
      const key = `${group}:${scenario.behavior_key || scenario.id}`;
      let combined = operations.get(key);
      if (!combined) {
        combined = { ...scenario, id: `guide-pon-${vendor}-${group}-${scenario.behavior_key || scenario.id}`,
          categoryId: `guide-pon-${vendor}-${group}`, variants: [] };
        operations.set(key, combined);
      }
      combined.variants.push(...(scenario.variants || [scenario]));
    }
  }
  const scenarios = [...operations.values()].map((scenario) => {
    const forms = scenario.variants;
    // Ownership comes only from the individually validated profile catalogs.
    // Sharing one menu never makes an ONU command valid on an OLT terminal.
    scenario.variantField = null;
    const normal = selectPackageGuideVariant(scenario);
    Object.assign(scenario, normal, { id: scenario.id });
    scenario.summary = normal?.documentation_only
      ? normal.interface === "Web" ? "在设备网页中操作" : normal.interface === "EMS/TL1" ? "在网管系统中操作" : "查看操作说明"
      : "查看命令，填入当前终端";
    scenario.fields = scenario.parameters.map(parameterField);
    scenario.buildGuide = (form) => buildPackageGuide(scenario, form);
    return scenario;
  });
  return { scenarios, groups: [...groups.values()], message: scenarios.length ? "" : catalogs.map((c) => c.message).filter(Boolean).join("；"),
    modelSummary: [...new Set(catalogs.map((c) => c.modelSummary).filter(Boolean))].join(" · ") };
}

export function packageGuideScenarios(states, deviceTypes = DEVICE_TYPES) {
  const installed = new Set(deviceTypes.flatMap((type) => type.profiles.map((p) => p.value)));
  const used = new Set();
  const result = PON_BRANDS.flatMap(([vendor]) => {
    const profiles = deviceTypes.flatMap((type) => type.profiles.map((p) => p.value))
      .filter((id) => id === `${vendor}_olt` || id === `${vendor}_onu`);
    profiles.forEach((id) => used.add(id));
    return mergedPonCatalog(vendor, states, profiles).scenarios;
  });
  return [...result, ...Object.entries(states).filter(([id]) => installed.has(id) && !used.has(id)).flatMap(([, state]) => state.catalog?.scenarios || [])];
}

// Browsing is independent of the terminal connection. The installed profile
// registry supplies permanent roots/brands; each brand's operations load lazily.
export function packageGuideTree(states, deviceTypes = DEVICE_TYPES) {
  const installed = new Set(deviceTypes.flatMap((type) => type.profiles.filter((p) => p.guideSource === "package").map((p) => p.value)));
  const used = new Set();
  const branches = PON_BRANDS.flatMap(([vendor, label, models]) => {
    const profiles = [`${vendor}_olt`, `${vendor}_onu`].filter((id) => installed.has(id));
    if (!profiles.length) return [];
    profiles.forEach((id) => used.add(id));
    const catalog = mergedPonCatalog(vendor, states, profiles);
    return [{ id: `package-guide-brand-pon-${vendor}`, label, summary: catalog.modelSummary || models,
      icon: Terminal, packageBrand: `pon-${vendor}`, packageProfile: profiles[0], packageProfiles: profiles,
      groups: catalog.groups, catalog: catalog.scenarios.length ? catalog : null }];
  });
  const others = deviceTypes.flatMap((type) => {
    const profiles = type.profiles.filter((profile) => profile.guideSource === "package");
    const remaining = profiles.filter((profile) => !used.has(profile.value));
    if (!remaining.length) return [];
    return [{
      id: `package-guide-type-${type.value}`, label: `${type.label}运维`,
      summary: "按品牌和常用操作查找命令", icon: Network, package: true,
      branches: remaining.map((profile) => ({
        id: `package-guide-brand-${profile.value}`, label: profile.label,
        summary: "按常用操作查看该品牌的指令速查", icon: Terminal,
        packageProfile: profile.value, groups: states[profile.value]?.catalog?.groups || [],
      })),
    }];
  });
  return [...(branches.length ? [{ id: "package-guide-type-pon", label: "OLT/ONU运维",
    summary: "按品牌和常用操作查找命令", icon: Network, package: true, branches }] : []), ...others];
}

export function guideDispatchBlockReason(scenario, target, canRun) {
  if (scenario?.documentation_only) return `${scenario.interface || "手册"} 参考内容，不能直接填入终端`;
  if (!canRun || target?.status !== "connected") return "请先连接设备，当前仅供浏览";
  if (scenario?.runtime && (target.deviceType !== scenario.deviceType || target.deviceProfile !== scenario.deviceProfile)) {
    return `仅可填入${deviceTypeDisplayName(scenario.deviceType)} · ${deviceProfileDisplayName(scenario.deviceProfile)}；当前设备类型或品牌不匹配`;
  }
  return "";
}
