export const DEVICE_TYPES = [
  {
    value: "server",
    label: "服务器",
    profiles: [
      {
        value: "linux",
        label: "Linux",
        completionLabel: "Linux 命令补全",
        guideRootIds: ["linux-ops"],
        guideBranchIds: [],
      },
    ],
  },
  {
    value: "switch",
    label: "交换机",
    profiles: [
      {
        value: "h3c_comware",
        label: "H3C（Comware）",
        completionLabel: "H3C Comware 命令补全",
        guideRootIds: ["switch-ops"],
        guideBranchIds: ["switch-h3c"],
      },
      {
        value: "huawei_vrp", label: "华为（VRP）", completionLabel: "华为 VRP 命令库",
        guideRootIds: ["switch-ops"], guideBranchIds: ["switch-huawei"],
      },
      {
        value: "cisco_ios", label: "思科（IOS / IOS XE）", completionLabel: "思科命令库（注意 IOS / NX-OS 适用范围）",
        guideRootIds: ["switch-ops"], guideBranchIds: ["switch-cisco"],
      },
      {
        value: "zte_zxr10", label: "中兴（ZXR10）", completionLabel: "中兴 ZXR10 命令库",
        guideRootIds: ["switch-ops"], guideBranchIds: ["switch-zte"],
      },
    ],
  },
];

const TYPE_BY_VALUE = new Map(DEVICE_TYPES.map((item) => [item.value, item]));
const PROFILE_BY_VALUE = new Map(
  DEVICE_TYPES.flatMap((type) =>
    type.profiles.map((profile) => [profile.value, { ...profile, deviceType: type.value }]),
  ),
);

export function deviceProfilesForType(deviceType) {
  return TYPE_BY_VALUE.get(deviceType)?.profiles || [];
}

export function defaultDeviceProfile(deviceType) {
  return deviceProfilesForType(deviceType)[0]?.value || "";
}

export function isDeviceClassificationValid(deviceType, deviceProfile) {
  return PROFILE_BY_VALUE.get(deviceProfile)?.deviceType === deviceType;
}

export function deviceTypeDisplayName(deviceType) {
  return TYPE_BY_VALUE.get(deviceType)?.label || "未分类";
}

export function deviceProfileDisplayName(deviceProfile) {
  return PROFILE_BY_VALUE.get(deviceProfile)?.label || "未选择子类型";
}

export function deviceProfileCompletionLabel(deviceProfile) {
  return PROFILE_BY_VALUE.get(deviceProfile)?.completionLabel || "命令补全";
}

export function deviceProfileConfig(deviceProfile) {
  return PROFILE_BY_VALUE.get(deviceProfile) || null;
}

export async function loadDeviceProfiles() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3000);
  try {
    const base = import.meta.env?.VITE_AUDITBOX_API_BASE_URL || "";
    const response = await fetch(`${base}/api/device-profiles`, { signal: controller.signal, cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    for (const row of data.profiles || []) {
      if (!/^[a-z][a-z0-9_-]{0,63}$/.test(row.profile_id) || !/^[a-z][a-z0-9_-]{0,63}$/.test(row.device_type) || PROFILE_BY_VALUE.has(row.profile_id)) continue;
      let type = TYPE_BY_VALUE.get(row.device_type);
      if (!type) {
        type = { value: row.device_type, label: row.category_label || row.device_type, profiles: [] };
        DEVICE_TYPES.push(type);
        TYPE_BY_VALUE.set(type.value, type);
      }
      const profile = { value: row.profile_id, label: row.label, completionLabel: `${row.label} 命令补全`, guideSource: "package", guideRootIds: [], guideBranchIds: [] };
      type.profiles.push(profile);
      PROFILE_BY_VALUE.set(profile.value, { ...profile, deviceType: type.value });
    }
  } catch { /* Older/offline boards retain their built-in profiles. */ }
  finally { clearTimeout(timer); }
}
