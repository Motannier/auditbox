"""Public product branding shared by the gateway, relay, and local prompts."""

from auditbox.env import get_str

DEFAULT_PRODUCT_NAME = "轻量化调试推理工具"


def product_name() -> str:
    # Names also appear in terminal notices: reject control/escape characters.
    value = get_str("AUDITBOX_PRODUCT_NAME", DEFAULT_PRODUCT_NAME).strip()
    if not value or len(value) > 80 or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        return DEFAULT_PRODUCT_NAME
    return value
