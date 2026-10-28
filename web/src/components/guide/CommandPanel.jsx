import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import {
  guideScenarios,
  skillTreeForDeviceProfile,
  createInitialGuideForm,
  buildOfflineGuide,
  findScenario,
} from "../../lib/guide/index.js";
import { GuideForm } from "./GuideForm.jsx";
import { DEVICE_TYPES, deviceProfileConfig } from "../../lib/deviceProfiles.js";
import { GUIDE_VARIANT_FIELD, guideDispatchBlockReason, packageGuideCatalog, packageGuideCounts, packageGuideScenarios, packageGuideTree, resolvePackageGuideScenario, selectPackageGuideVariant } from "../../lib/guide/packageGuide.js";
import { fetchCommandGuide, fetchCommandGuideCounts } from "../../services/commandGuideService.js";

function useGuideCounts(enabled) {
  const [state, setState] = useState({ counts: {}, loading: true, error: "" });
  const request = useRef(null);
  const load = useCallback(() => {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setState({ counts: {}, loading: true, error: "" });
    fetchCommandGuideCounts({ signal: controller.signal }).then((data) => {
      if (!controller.signal.aborted) setState({ counts: packageGuideCounts(data), loading: false, error: "" });
    }).catch((error) => {
      if (!controller.signal.aborted) setState({ counts: {}, loading: false, error: error.message || "操作数量加载失败" });
    }).finally(() => {
      if (request.current === controller) request.current = null;
    });
  }, []);
  useEffect(() => {
    if (enabled) load();
    return () => { request.current?.abort(); request.current = null; };
  }, [enabled, load]);
  return [state, load];
}

function usePackageGuides() {
  const [states, setStates] = useState({});
  const entries = useRef({});
  const requests = useRef(new Map());
  const load = useCallback((profile, retry = false) => {
    if (requests.current.has(profile) || (!retry && entries.current[profile])) return;
    const controller = new AbortController();
    requests.current.set(profile, controller);
    const publish = (entry) => {
      entries.current[profile] = entry;
      setStates((current) => ({ ...current, [profile]: entry }));
    };
    publish({ loading: true });
    fetchCommandGuide(profile, { signal: controller.signal }).then((data) => {
      if (!controller.signal.aborted) publish({ catalog: packageGuideCatalog(data, profile) });
    }).catch((error) => {
      if (!controller.signal.aborted) publish({ error: error.message || "指令速查加载失败" });
    }).finally(() => {
      if (requests.current.get(profile) === controller) requests.current.delete(profile);
    });
  }, []);
  useEffect(() => () => {
    for (const [profile, controller] of requests.current) {
      controller.abort();
      delete entries.current[profile];
    }
    requests.current.clear();
  }, []);
  return [states, load];
}

export function CommandPanel({
  onRunCommands,
  onContinueCommands,
  onCancelCommands,
  onGuideEvent,
  guideQueue,
  target,
  canRun,
}) {
  const [selectedScenario, setSelectedScenario] = useState(null);
  const [expandedCategories, setExpandedCategories] = useState({});
  const [form, setForm] = useState({});
  const [dispatchError, setDispatchError] = useState("");
  const [packageStates, loadPackage] = usePackageGuides();
  const profile = target?.deviceProfile || "";
  const usesPackage = deviceProfileConfig(profile)?.guideSource === "package";
  const staticTree = useMemo(() => skillTreeForDeviceProfile(profile), [profile]);
  const packageTypes = useMemo(() => {
    if (!profile) return DEVICE_TYPES;
    const selectedType = deviceProfileConfig(profile)?.deviceType || target?.deviceType;
    return DEVICE_TYPES.filter(({ value }) => ["olt", "onu"].includes(selectedType)
      ? ["olt", "onu"].includes(value) : value === selectedType);
  }, [profile, target?.deviceType]);
  const scenarios = useMemo(() => {
    const staticCategories = new Set(staticTree.flatMap((domain) => domain.branches.flatMap((branch) =>
      branch.groups?.length ? branch.groups.map((group) => group.id) : [branch.id],
    )));
    return [...guideScenarios.filter((scenario) => staticCategories.has(scenario.categoryId)),
      ...packageGuideScenarios(packageStates, packageTypes)];
  }, [staticTree, packageStates, packageTypes]);
  const skillTree = useMemo(() => [...staticTree, ...packageGuideTree(packageStates, packageTypes)], [staticTree, packageStates, packageTypes]);
  const hasPackages = skillTree.some((domain) => domain.package);
  const [countState, loadCounts] = useGuideCounts(hasPackages);

  useEffect(() => {
    if (usesPackage) loadPackage(profile);
  }, [profile, usesPackage, loadPackage]);

  function scenariosIn(categoryId) {
    return scenarios.filter((scenario) => scenario.categoryId === categoryId);
  }

  function branchScenarioCount(branch) {
    if (branch.packageProfiles) {
      if (branch.packageProfiles.every((id) => packageStates[id]?.catalog)) return branch.catalog?.scenarios.length || 0;
      return countState.counts[branch.packageBrand]?.count ?? null;
    }
    if (branch.packageProfile) {
      const catalog = packageStates[branch.packageProfile]?.catalog;
      return catalog ? catalog.scenarios.length : countState.counts[branch.packageProfile]?.count ?? null;
    }
    return branch.groups?.length
      ? branch.groups.reduce((total, group) => total + scenariosIn(group.id).length, 0)
      : scenariosIn(branch.id).length;
  }

  function renderCount(count) {
    if (count != null) return <b title={`${count} 项常用操作`}>{count}</b>;
    const loading = countState.loading;
    return <b aria-label={loading ? "正在加载操作数量" : "操作数量暂不可用"}
      title={loading ? "正在加载操作数量" : "操作数量暂不可用，可展开品牌查看详情"}>{loading ? "…" : "!"}</b>;
  }

  const baseScenario = selectedScenario ? findScenario(selectedScenario, scenarios) : null;
  function initialForm(scenario) {
    if (!scenario) return {};
    const matching = selectPackageGuideVariant(scenario, target);
    if (!matching) return createInitialGuideForm(scenario.id, target, [scenario]);
    const selected = resolvePackageGuideScenario(scenario, { [GUIDE_VARIANT_FIELD]: matching.id });
    return { ...createInitialGuideForm(selected.id, target, [selected]), [GUIDE_VARIANT_FIELD]: matching.id };
  }
  useEffect(() => {
    if (!baseScenario) {
      setForm({});
      setDispatchError("");
      return;
    }
    setForm(initialForm(baseScenario));
    setDispatchError("");
  }, [baseScenario, target?.id, target?.status, target?.deviceProfile, target?.deviceType, target?.host, target?.port, target?.protocol]);

  const currentScenario = useMemo(() => resolvePackageGuideScenario(baseScenario, form), [baseScenario, form]);

  const dispatchBlockReason = guideDispatchBlockReason(currentScenario, target, canRun);
  const effectiveForm = useMemo(() => {
    if (!currentScenario) return {};
    const formIsReady = currentScenario.fields.every((field) =>
      Object.prototype.hasOwnProperty.call(form, field.key),
    );
    return formIsReady ? form : { ...createInitialGuideForm(currentScenario.id, target, [currentScenario]), ...form };
  }, [currentScenario, form, target, scenarios]);
  const guideResult = useMemo(() => {
    if (!selectedScenario || !currentScenario) return null;
    return buildOfflineGuide(selectedScenario, effectiveForm, target, scenarios);
  }, [currentScenario, effectiveForm, selectedScenario, target, scenarios]);

  function toggleExpanded(id) {
    setExpandedCategories((current) => ({ ...current, [id]: !current[id] }));
  }

  function updateField(field, value) {
    if (guideQueue?.scenarioId === baseScenario?.id && ["active", "waiting", "paused"].includes(guideQueue.status)) return;
    if (field === GUIDE_VARIANT_FIELD) return; // Internal selection is never a user field.
    setForm((current) => ({ ...current, [field]: value }));
    setDispatchError("");
  }

  function startGuide(event) {
    event.preventDefault();
    if (dispatchBlockReason || !guideResult?.commands.length || !currentScenario
        || (guideQueue?.scenarioId === currentScenario.id && ["active", "waiting", "paused"].includes(guideQueue.status))) return;
    const result = buildOfflineGuide(selectedScenario, effectiveForm, target, scenarios);
    if (!result.commands.length) return;
    const started = onRunCommands({
      scenarioId: currentScenario.id,
      title: currentScenario.title,
      commands: result.commands,
      parameters: effectiveForm,
    });
    setDispatchError(started ? "" : "终端尚未就绪，请确认连接状态后重试");
  }

  function selectScenario(scenarioId) {
    const next = selectedScenario === scenarioId ? null : scenarioId;
    setSelectedScenario(next);
    setForm(next ? initialForm(findScenario(next, scenarios)) : {});
    if (next) onGuideEvent?.("guide_scenario_opened", { scenario_id: next });
    setDispatchError("");
  }

  function renderScenarioLeaves(categoryId) {
    return (
      <div className="offline-skill-leaves">
        {scenariosIn(categoryId).map((scenario) => {
          const ScenarioIcon = scenario.icon;
          const active = scenario.id === selectedScenario;
          return (
            <section className={`offline-skill-leaf-shell ${active ? "active" : ""}`} key={scenario.id} data-guide-scenario={scenario.id}>
              <button
                className={`offline-skill-leaf ${active ? "active" : ""}`}
                onClick={() => selectScenario(scenario.id)}
                aria-expanded={active}
              >
                {active ? (
                  <ChevronDown className="leaf-chevron" size={14} />
                ) : (
                  <ChevronRight className="leaf-chevron" size={14} />
                )}
                <ScenarioIcon size={15} />
                <span>
                  <strong>{scenario.title}</strong>
                  <small>{scenario.summary}</small>
                </span>
              </button>
              {active && currentScenario ? (
                <div className="offline-skill-leaf-panel">
                  <GuideForm
                    currentScenario={currentScenario}
                    form={effectiveForm}
                    guideResult={guideResult}
                    onSubmit={startGuide}
                    onUpdateField={updateField}
                    guideQueue={guideQueue?.scenarioId === currentScenario.id ? guideQueue : null}
                    dispatchError={dispatchError}
                    onContinue={onContinueCommands}
                    onCancel={onCancelCommands}
                    canRun={canRun && !dispatchBlockReason}
                    runDisabledReason={dispatchBlockReason}
                  />
                </div>
              ) : null}
            </section>
          );
        })}
      </div>
    );
  }

  return (
    <section className="command-panel-view" aria-label="指令速查内容">
      <div className="guide-shell" aria-label="指令速查">
        {countState.error && hasPackages ? (
          <div className="guide-package-notice" role="status">
            <span>{countState.error}</span>
            <button type="button" onClick={loadCounts}>重试统计</button>
          </div>
        ) : null}
        <div className="offline-skill-tree" role="tree" aria-label="指令速查分类">
          {skillTree.map((domain) => {
            const DomainIcon = domain.icon;
            const open = Boolean(expandedCategories[domain.id]);
            const branchCounts = domain.branches.map(branchScenarioCount);
            const leafCount = branchCounts.some((count) => count == null) ? null : branchCounts.reduce((total, count) => total + count, 0);
            return (
              <section className={`offline-skill-group ${open ? "open" : ""}`} key={domain.id} data-guide-domain={domain.id}>
                <button
                  className="offline-skill-trigger offline-skill-root"
                  onClick={() => toggleExpanded(domain.id)}
                  aria-expanded={open}
                >
                  {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                  <span className="skill-icon root-icon">
                    <DomainIcon size={16} />
                  </span>
                  <span>
                    <strong>{domain.label}</strong>
                    <small>{domain.summary}</small>
                  </span>
                  {renderCount(leafCount)}
                </button>
                {open ? (
                  <div className="offline-skill-branches">
                    {domain.branches.map((branch) => {
                      const BranchIcon = branch.icon;
                      const branchOpen = Boolean(expandedCategories[branch.id]);
                      const scenarioCount = branchScenarioCount(branch);
                      const packageState = branch.packageProfiles ? {
                        catalog: branch.catalog || (branch.packageProfiles.every((id) => packageStates[id]?.catalog) ? { scenarios: [], groups: [] } : null),
                        error: branch.packageProfiles.map((id) => packageStates[id]?.error).filter(Boolean).join("；"),
                      } : packageStates[branch.packageProfile];
                      return (
                        <section className={`offline-skill-branch ${branchOpen ? "open" : ""}`} key={branch.id} data-guide-profile={branch.packageProfile}>
                          <button
                            className="offline-skill-branch-trigger"
                            onClick={() => {
                              toggleExpanded(branch.id);
                              if (!branchOpen) for (const id of branch.packageProfiles || (branch.packageProfile ? [branch.packageProfile] : [])) loadPackage(id);
                            }}
                            aria-expanded={branchOpen}
                          >
                            {branchOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                            <span className="skill-icon">
                              <BranchIcon size={15} />
                            </span>
                            <span>
                              <strong>{branch.label}</strong>
                              <small title={branch.summary}>{branch.summary}</small>
                            </span>
                            {renderCount(scenarioCount)}
                          </button>
                          {branchOpen ? (
                            branch.packageProfile && !packageState?.catalog ? (
                              <div className="guide-package-notice" role="status">
                                {packageState?.error ? <>
                                  <span>{packageState.error}</span>
                                  <button type="button" onClick={() => {
                                    for (const id of branch.packageProfiles || [branch.packageProfile]) if (packageStates[id]?.error) loadPackage(id, true);
                                  }}>重试</button>
                                </> : "正在加载该品牌的常用操作…"}
                              </div>
                            ) : <>
                              {packageState?.error ? <div className="guide-package-notice" role="status">
                                <span>{packageState.error}；已加载的操作仍可查看。</span>
                                <button type="button" onClick={() => {
                                  for (const id of branch.packageProfiles || [branch.packageProfile]) if (packageStates[id]?.error) loadPackage(id, true);
                                }}>重试</button>
                              </div> : null}
                              {packageState?.catalog?.message ? (
                                <p className="guide-package-notice" role="status">{packageState.catalog.message}</p>
                              ) : null}
                              {branch.groups?.length ? (
                                <div className="offline-skill-functions">
                                  {branch.groups.map((group) => {
                                    const GroupIcon = group.icon;
                                    const groupOpen = Boolean(expandedCategories[group.id]);
                                    const groupCount = scenariosIn(group.id).length;
                                    return (
                                      <section
                                        className={`offline-skill-function ${groupOpen ? "open" : ""}`}
                                        key={group.id}
                                      >
                                        <button
                                          className="offline-skill-function-trigger"
                                          onClick={() => toggleExpanded(group.id)}
                                          aria-expanded={groupOpen}
                                        >
                                          {groupOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                                          <GroupIcon size={14} />
                                          <span>
                                            <strong>{group.label}</strong>
                                            <small>{group.summary}</small>
                                          </span>
                                          <b>{groupCount}</b>
                                        </button>
                                        {groupOpen ? renderScenarioLeaves(group.id) : null}
                                      </section>
                                    );
                                  })}
                                </div>
                              ) : (
                                renderScenarioLeaves(branch.id)
                              )}
                            </>
                          ) : null}
                        </section>
                      );
                    })}
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      </div>
    </section>
  );
}
