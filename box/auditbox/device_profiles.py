"""Canonical device classification and capability metadata.

``device_type`` is the broad hardware category shown by the first menu while
``device_profile`` identifies the platform/OS selected by the second menu.
All runtime feature routing must use the profile instead of guessing from the
connection protocol or terminal banner.
"""

from __future__ import annotations

from dataclasses import dataclass


SERVER = "server"
SWITCH = "switch"
LINUX = "linux"
H3C_COMWARE = "h3c_comware"
HUAWEI_VRP = "huawei_vrp"
CISCO_IOS = "cisco_ios"
ZTE_ZXR10 = "zte_zxr10"
DeviceProfileKey = str  # Actual values are validated through the installed registry.
SWITCH_PROFILES = (H3C_COMWARE, HUAWEI_VRP, CISCO_IOS, ZTE_ZXR10)
REFERENCE_PROFILES = (HUAWEI_VRP, CISCO_IOS, ZTE_ZXR10)


@dataclass(frozen=True)
class DeviceProfile:
    key: str
    device_type: str
    label: str
    recommendation_namespace: str
    line_clear_sequence: bytes
    guide_roots: tuple[str, ...]
    guide_branches: tuple[str, ...] = ()
    pager_prompt_pattern: str = ""
    command_error_pattern: str = ""
    ready_prompt_pattern: str = ""
    line_redraw_sequence: bytes = b""


DEVICE_PROFILES: dict[str, DeviceProfile] = {
    LINUX: DeviceProfile(
        key=LINUX,
        device_type=SERVER,
        label="Linux",
        recommendation_namespace=LINUX,
        line_clear_sequence=b"\x15",  # Ctrl+U
        guide_roots=("linux-ops",),
    ),
    H3C_COMWARE: DeviceProfile(
        key=H3C_COMWARE,
        device_type=SWITCH,
        label="H3C Comware",
        recommendation_namespace="h3c",
        line_clear_sequence=b"\x18",  # Ctrl+X on Comware
        line_redraw_sequence=b"\x12",  # Ctrl+R redisplays the Comware editor.
        guide_roots=("switch-ops",),
        guide_branches=("switch-h3c",),
        pager_prompt_pattern=r"(?:-{2,}\s*more\s*-{2,})\s*$",
        command_error_pattern=(
            r"(?im)^%\s*(?:unrecognized command|wrong parameter|"
            r"incomplete command|too many parameters|command cannot be executed|"
            r"permission denied|error|failed)\b"
        ),
        ready_prompt_pattern=(
            r"(?m)(?:^|[\r\n])(?:<[^<>\r\n]{1,128}>|"
            r"\[[^\[\]\r\n]{1,128}\])\s*$"
        ),
    ),
}

DEVICE_PROFILE_DEFAULTS = {
    SERVER: LINUX,
    SWITCH: H3C_COMWARE,
}

# Each brand has an independent recommendation namespace and terminal grammar.
# They must not inherit either the Linux graph or the H3C fine-tuned reviewer.
for _key, _label, _vendor, _clear in (
    (HUAWEI_VRP, "华为（VRP）", "huawei", b"\x15"),
    (CISCO_IOS, "思科（IOS / IOS XE）", "cisco", b"\x15"),
    (ZTE_ZXR10, "中兴（ZXR10）", "zte", b"\x15"),
):
    DEVICE_PROFILES[_key] = DeviceProfile(
        key=_key, device_type=SWITCH, label=_label,
        recommendation_namespace=_vendor, line_clear_sequence=_clear,
        line_redraw_sequence=b"\x12" if _key == CISCO_IOS else b"",
        guide_roots=("switch-ops",), guide_branches=(f"switch-{_vendor}",),
        pager_prompt_pattern=r"(?:-{2,}\s*more\s*-{2,}|more:)\s*$",
        command_error_pattern=(
            r"(?im)^\s*(?:%\s*|Error:\s*)(?:unrecognized|unknown|invalid|incomplete|"
            r"ambiguous|wrong parameter|too many|permission denied|error|failed|"
            r"command cannot|unrecognized command)\b"
        ),
        ready_prompt_pattern=(
            r"(?m)(?:^|[\r\n])(?:<[^<>\r\n]{1,128}>|\[[^\[\]\r\n]{1,128}\])\s*$"
            if _key == HUAWEI_VRP else
            r"(?m)(?:^|[\r\n])[\w.-]{1,128}(?:\([\w.-]+\))?\s*[#>]\s*$"
        ),
    )

_PROFILE_ALIASES = {
    "h3c": H3C_COMWARE,
    "comware": H3C_COMWARE,
    "huawei": HUAWEI_VRP,
    "cisco": CISCO_IOS,
    "zte": ZTE_ZXR10,
}


def normalize_device_type(value: str) -> str:
    return str(value or "").strip().lower()


def normalize_device_profile(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return _PROFILE_ALIASES.get(normalized, normalized)


def default_device_profile(device_type: str) -> str:
    """Return the migration/legacy default for a supported broad type."""
    return DEVICE_PROFILE_DEFAULTS.get(normalize_device_type(device_type), "")


def get_device_profile(device_profile: str) -> DeviceProfile | None:
    key = normalize_device_profile(device_profile)
    builtin = DEVICE_PROFILES.get(key)
    if builtin:
        return builtin
    from auditbox.command_packages import metadata
    meta = metadata(key)
    if not meta:
        return None
    return DeviceProfile(key=key, device_type=meta['profile']['device_type'],
        label=meta['profile']['label'], recommendation_namespace='package_' + key,
        line_clear_sequence=b'\x15', guide_roots=())


def require_device_profile(device_type: str, device_profile: str) -> DeviceProfile:
    """Validate the two-level classification and return its capability record."""
    normalized_type = normalize_device_type(device_type)
    profile = get_device_profile(device_profile)
    if profile is None:
        raise ValueError("请选择受支持的设备子类型")
    if profile.device_type != normalized_type:
        raise ValueError("设备子类型与设备大类不匹配")
    return profile


def profile_display_name(device_profile: str) -> str:
    profile = get_device_profile(device_profile)
    return profile.label if profile else str(device_profile or "")


def recommendation_namespace(protocol: str, device_profile: str) -> str:
    profile = get_device_profile(device_profile)
    if profile is None:
        return str(protocol or "").strip().lower()
    return f"{str(protocol or '').strip().lower()}_{profile.recommendation_namespace}"


__all__ = [
    "DEVICE_PROFILES",
    "DEVICE_PROFILE_DEFAULTS",
    "H3C_COMWARE",
    "HUAWEI_VRP",
    "CISCO_IOS",
    "ZTE_ZXR10",
    "DeviceProfileKey",
    "SWITCH_PROFILES",
    "REFERENCE_PROFILES",
    "LINUX",
    "SERVER",
    "SWITCH",
    "DeviceProfile",
    "default_device_profile",
    "get_device_profile",
    "normalize_device_profile",
    "normalize_device_type",
    "profile_display_name",
    "recommendation_namespace",
    "require_device_profile",
]
