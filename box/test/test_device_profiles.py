import pytest

from auditbox.device_profiles import (
    default_device_profile,
    recommendation_namespace,
    require_device_profile,
)


def test_supported_two_level_device_classifications() -> None:
    linux = require_device_profile("server", "linux")
    h3c = require_device_profile("switch", "h3c_comware")

    assert linux.line_clear_sequence == b"\x15"
    assert h3c.line_clear_sequence == b"\x18"
    assert default_device_profile("server") == "linux"
    assert default_device_profile("switch") == "h3c_comware"
    assert recommendation_namespace("ssh", linux.key) == "ssh_linux"
    assert recommendation_namespace("console", h3c.key) == "console_h3c"


@pytest.mark.parametrize(
    ("device_type", "device_profile"),
    [
        ("server", "h3c_comware"),
        ("switch", "linux"),
        ("switch", ""),
    ],
)
def test_mismatched_or_missing_profiles_are_rejected(
    device_type: str,
    device_profile: str,
) -> None:
    with pytest.raises(ValueError):
        require_device_profile(device_type, device_profile)
