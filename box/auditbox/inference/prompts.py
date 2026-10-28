"""Shared prompts used by the local assistant and startup cache warmup."""

from __future__ import annotations

import json
from typing import Any

from auditbox.device_profiles import H3C_COMWARE, LINUX, normalize_device_profile
from auditbox.branding import product_name

ASSISTANT_SYSTEM_PROMPT = "你是“" + product_name() + """”的本地中文运维助手，SSH、Telnet、IP、Linux 等专有名词保留原样。
针对 Linux、网络、防火墙和交换机问题，给出简洁、准确、可复制的步骤。你不能操作终端或声称已执行命令；命令用代码块并说明预期结果。
涉及重启、删除、覆盖配置、路由或防火墙变更时，说明风险、影响和回退方法。信息不足时明确索要必要输出，不得编造设备状态。"""

HISTORY_REVIEW_SYSTEM_PROMPT = "你是" + product_name() + """的实时安全审查器。根据一段连续、已脱敏的终端命令历史判断操作者的综合意图。
同时独立判断 current_seq 指定的当前命令：current.risk 只表示这条命令本身的影响，不能继承历史最高风险。历史 reboot、删除或取消的危险操作之后执行只读查询，当前查询仍是 low；危险链路另在总体 risk、reason、evidence 中说明，并列出确切相关序号。
你不负责逐条命令放行，只识别连续操作中已经显现的破坏、绕过安全控制、批量删除、擦除配置、持久化攻击或隐藏执行意图。
正常查询、排障、查看日志、常规配置和单纯重启不得判为严重风险。运维中删除明确文件、缓存、构建产物或业务目录可能是正常操作，不能仅因出现删除命令就判为严重风险。
历史项的 status=blocked 表示下发前阻断，status=cancelled 表示已取消，status=failed 表示设备拒绝或未确认生效。这些项可以作为意图证据，但不能当作已执行或设备已改变的事实，更不能使后续无关命令继承高危。
status=executed 只表示已下发，不代表成功生效；不要把临时停止服务等可恢复操作描述成不可逆破坏。
历史项中的 authorized=true 表示用户已经在当前目标上仅授权该命令执行一次；保留其风险等级，不重复发起确认。该授权不覆盖后续命令。
只有删除系统关键路径、扩大到不可控范围，或同时存在编码拆分、隐藏执行、绕过审计、清理痕迹等明确证据时，才可将删除意图判为 critical。
你只提供风险判断和证据，不执行或中断命令，不请求锁定，也不得声称盒子或终端已锁定。兼容字段 lock 固定为 false。
只返回一个 JSON 对象，不要 Markdown：
{"risk":"low|medium|high|critical","intent":"一句话意图","reason":"历史及链路判断依据","evidence":[相关命令序号],"current":{"seq":当前序号,"risk":"low|medium|high|critical|unknown","reason":"仅当前命令的判断依据"},"confidence":0到1,"lock":false}
risk=critical 仍须保留严重风险原因和证据，仅用于告警和审计。"""


def assistant_system_prompt(device_profile: str = LINUX) -> str:
    """Build a platform-specific assistant prompt from the selected profile."""
    normalized = normalize_device_profile(device_profile) or LINUX
    if normalized == H3C_COMWARE:
        return (
            ASSISTANT_SYSTEM_PROMPT
            + "\n当前设备子类型是 H3C Comware 交换机。所有命令必须使用 H3C "
            "Comware 语法；查询命令优先使用 display，禁止输出 Cisco 的 show "
            "语法或 Linux 命令。无法确认命令时先要求用户提供 display version，"
            "不得猜测命令。"
        )
    return (
        ASSISTANT_SYSTEM_PROMPT
        + "\n当前设备子类型是 Linux 服务器。命令必须使用 Linux 用户态工具和 shell "
        "语法，不得输出 H3C Comware 或 Cisco 网络设备命令。"
    )


def assistant_warmup_payload(
    model: str,
    device_profile: str = LINUX,
) -> dict[str, Any]:
    """Build a request whose stable prefix matches normal assistant requests."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": assistant_system_prompt(device_profile)},
            {"role": "user", "content": "准备就绪"},
        ],
        "temperature": 0,
        "top_p": 0.9,
        "max_tokens": 1,
        "stream": False,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def review_warmup_payload(
    model: str,
    device_profile: str = LINUX,
) -> dict[str, Any]:
    """Build a request whose stable prefix matches operation-review requests."""
    review_input = {
        "protocol": "",
        "device_type": "",
        "device_profile": normalize_device_profile(device_profile) or LINUX,
        "current_seq": 0,
        "history": [],
    }
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": HISTORY_REVIEW_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    review_input,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ],
        "temperature": 0,
        "top_p": 0.1,
        "max_tokens": 1,
        "stream": False,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
