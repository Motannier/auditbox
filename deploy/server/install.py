#!/usr/bin/env python3
"""Install only the AuditBox server application; never modify host networking.

Run as root after unpacking a source release into /opt/auditbox-server/releases/.
The two model containers are managed separately using models.compose.yaml.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import secrets
import subprocess

ROOT = Path('/opt/auditbox-server')
CONFIG = Path('/etc/auditbox-server')
STATE = Path('/var/lib/auditbox-server')
LOGS = Path('/var/log/auditbox-server')
USER = 'auditbox-server'


def run(*command):
    subprocess.run(command, check=True)


def write(path, content, mode=0o644):
    path.write_text(content)
    path.chmod(mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--address', required=True)
    args = parser.parse_args()
    release = args.release.resolve(strict=True)
    if os.getuid() != 0 or not release.is_relative_to(ROOT / 'releases'):
        raise SystemExit('Require root and an AuditBox release directory')
    for item in ('box/auditbox/gateway/app.py', 'agent/pyproject.toml', 'dist/index.html'):
        if not (release / item).is_file():
            raise SystemExit('Incomplete release: ' + item)
    current = ROOT / 'current'
    if current.exists() and not current.is_symlink():
        raise SystemExit('Refusing to overwrite a non-symlink current directory')
    if not (ROOT / 'bin/uv').exists():
        raise SystemExit('Install a private uv executable under /opt/auditbox-server/bin/uv first')
    try:
        account = pwd.getpwnam(USER)
    except KeyError:
        run('useradd', '--system', '--home-dir', str(STATE), '--shell', '/usr/sbin/nologin', USER)
        account = pwd.getpwnam(USER)
    for directory in (CONFIG, STATE, LOGS):
        directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    for directory in (STATE, LOGS):
        os.chown(directory, account.pw_uid, account.pw_gid)
    # Create environments locally to this application, with locked dependencies.
    # No pip install in the host's global Python environment.
    for module in ('box', 'agent'):
        venv = release / module / '.venv'
        if not (venv / 'bin/python').exists():
            run('python3', '-m', 'venv', str(venv))
        run(str(ROOT / 'bin/uv'), 'export', '--quiet', '--frozen', '--no-dev', '--no-emit-project',
            '--project', str(release / module), '--output-file', str(venv / 'requirements.txt'))
        run(str(ROOT / 'bin/uv'), 'pip', 'install', '--python', str(venv / 'bin/python'),
            '-r', str(venv / 'requirements.txt'))
    # First installation has fresh accounts and its own signing key. Upgrades
    # never reset credentials, history, imported packages, or the persisted ID.
    env = CONFIG / 'auditbox.env'
    if not env.exists():
        credentials = {
            'url': f'http://{args.address}:18080/',
            'opsadmin': secrets.token_urlsafe(16),
            'superadmin': secrets.token_urlsafe(20),
        }
        write(CONFIG / 'initial-credentials.json', json.dumps(credentials, indent=2) + '\n', 0o600)
        values = {
            'AUDITBOX_PRODUCT_NAME': '轻量化调试推理工具',
            'AUDITBOX_PORT': '19000', 'AUDITBOX_PROXY_PORT': '19000',
            'AUDITBOX_PROXY_HOST': '127.0.0.1', 'AUDITBOX_PREDICT_PORT': '19001',
            'AUDITBOX_GATEWAY_HOST': '127.0.0.1',
            'AUDITBOX_GATEWAY_HOSTS': f'127.0.0.1,{args.address}', 'AUDITBOX_GATEWAY_PORT': '18080',
            'AUDITBOX_GATEWAY_CORS_ORIGINS': f'http://{args.address}:18080',
            'AUDITBOX_PUBLIC_URL': credentials['url'].rstrip('/'),
            'AUDITBOX_WEB_DIST_DIR': str(ROOT / 'current/dist'),
            'AUDITBOX_DB_DIR': str(STATE), 'AUDITBOX_DB_PATH': str(STATE / 'auditbox.sqlite3'),
            'AUDITBOX_AUDIT_DIR': str(LOGS), 'AUDITBOX_GUARDIAN_REQUIRED': 'false',
            'AUDITBOX_BROWSER_CLOCK_SYNC_ENABLED': 'false', 'AUDITBOX_NETWORK_SETTINGS_ENABLED': 'false',
            'AUDITBOX_TARGET_PREPARE_SOCKET': '',
            'AUDITBOX_ADMIN_USERNAME': 'opsadmin', 'AUDITBOX_ADMIN_PASSWORD': credentials['opsadmin'],
            'AUDITBOX_SUPER_ADMIN_USERNAME': 'superadmin', 'AUDITBOX_SUPER_ADMIN_PASSWORD': credentials['superadmin'],
            'AUDITBOX_AUTH_SECRET': secrets.token_urlsafe(48),
            'AUDITBOX_GUARDIAN_SECRET': secrets.token_urlsafe(48),
            'AUDITBOX_AUTH_TOKEN_TTL': '43200',
            'AUDITBOX_INFERENCE_HOST': '127.0.0.1', 'AUDITBOX_INFERENCE_PORT': '18091',
            'AUDITBOX_INFERENCE_PROFILES': 'linux',
            'AUDITBOX_ASSISTANT_BACKEND_URL': 'http://127.0.0.1:18081',
            'AUDITBOX_REVIEW_BACKEND_URL': 'http://127.0.0.1:18082',
            'AUDITBOX_LLM_URL': 'http://127.0.0.1:18091/assistant',
            'AUDITBOX_HISTORY_REVIEW_LLM_URL': 'http://127.0.0.1:18091/review',
            'AUDITBOX_ASSISTANT_LLM_MODEL': 'qwen3.5-2b',
            'AUDITBOX_LINUX_REVIEW_LLM_MODEL': 'command-guard',
            'AUDITBOX_LLM_MODEL': 'command-guard', 'AUDITBOX_LLM_TIMEOUT': '180',
            'AUDITBOX_HISTORY_REVIEW_ENABLED': 'true',
            'AUDITBOX_AGENT_URL': 'http://127.0.0.1:18765',
            'AUDITBOX_AGENT_TIMEOUT': '180',
            'AUDITBOX_AGENT_HOST': '127.0.0.1', 'AUDITBOX_AGENT_PORT': '18765',
            'AUDITBOX_AGENT_ENVIRONMENT': 'production', 'AUDITBOX_AGENT_LOCAL_USER_ID': 'local-user',
            'AUDITBOX_AGENT_STANDARD_MODEL': 'qwen3.5-2b',
            'AUDITBOX_AGENT_MODEL_BACKEND': 'openai',
            'AUDITBOX_AGENT_MODEL_BASE_URL': 'http://127.0.0.1:18081/v1',
            'AUDITBOX_AGENT_STANDARD_MODEL_BASE_URL': 'http://127.0.0.1:18081/v1',
            'AUDITBOX_AGENT_MODEL_CONTEXT_WINDOW_TOKENS': '32768',
            'AUDITBOX_AGENT_GATEWAY_INTERNAL_URL': 'http://127.0.0.1:18080',
            'AUDITBOX_AGENT_DATABASE_URL': f'sqlite:///{STATE}/agent.sqlite3',
            'AUDITBOX_AGENT_DEFAULT_SKILL_DIRECTORY': str(ROOT / 'current/agent/defaults/skills'),
            'AUDITBOX_AGENT_SKILL_DIRECTORY': str(STATE / 'skills'),
            'AUDITBOX_AGENT_CONVERSATION_FILE_DIRECTORY': str(STATE / 'conversations'),
            'AUDITBOX_AGENT_WORKSPACE_DIRECTORY': str(STATE / 'workspace'),
            'AUDITBOX_AGENT_GENERATED_FILE_DIRECTORY': str(STATE / 'generated'),
            'AUDITBOX_AGENT_SQL_TOOL_DATABASE_URL': f'sqlite:///{STATE}/tool-workspace.sqlite3',
            'AUDITBOX_AGENT_EXECUTION_ENABLED': 'false', 'AUDITBOX_AGENT_KNOWLEDGE_ENABLED': 'false',
            'AUDITBOX_AGENT_KNOWLEDGE_UPLOAD_DIRECTORY': str(STATE / 'uploads'),
            'AUDITBOX_AGENT_KNOWLEDGE_CHROMA_DIRECTORY': str(STATE / 'chromadb'),
            'AUDITBOX_AGENT_STANDARD_MODEL_WARMUP_ENABLED': 'true',
            'AUDITBOX_AGENT_TELEMETRY_ENABLED': 'false',
        }
        write(env, ''.join(f'{key}={value}\n' for key, value in values.items()), 0o640)
        os.chown(env, 0, account.pw_gid)
    configured = env.read_text()
    for key, value in {
        'AUDITBOX_AGENT_KNOWLEDGE_UPLOAD_DIRECTORY': str(STATE / 'uploads'),
        'AUDITBOX_AGENT_KNOWLEDGE_CHROMA_DIRECTORY': str(STATE / 'chromadb'),
    }.items():
        if not any(line.startswith(key + '=') for line in configured.splitlines()):
            configured += f'{key}={value}\n'
    write(env, configured, 0o640)
    os.chown(env, 0, account.pw_gid)
    # All services share loopback but use non-conflicting ports. No root, time,
    # network-admin or Docker-socket access is granted to the application.
    units = Path('/etc/systemd/system')
    write(units / 'auditbox-server.slice', '''[Unit]
Description=AuditBox server application resource budget
[Slice]
CPUQuota=200%
CPUWeight=50
MemoryHigh=1536M
MemoryMax=2G
MemorySwapMax=0
''')
    services = {
        'proxy': ('box', 'box', 'auditbox.app.proxy_server', ''),
        'gateway': ('box', 'box', 'auditbox.gateway.server', 'auditbox-server-proxy.service'),
        'inference': ('box', 'box', 'auditbox.inference.server', 'auditbox-server-models.service'),
        'agent': ('agent', 'agent/src', 'auditbox_agent.main', 'auditbox-server-models.service'),
    }
    for name, (venv, module, entry, after) in services.items():
        write(units / f'auditbox-server-{name}.service', f'''[Unit]
Description=AuditBox server {name}
After=network.target {after}
Wants={after}
StartLimitIntervalSec=0
[Service]
Type=simple
User={USER}
Group={USER}
Slice=auditbox-server.slice
WorkingDirectory={ROOT}/current
EnvironmentFile={env}
Environment=PYTHONPATH={ROOT}/current/{module}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={ROOT}/current/{venv}/.venv/bin/python -m {entry}
Restart=always
RestartSec=5
NoNewPrivileges=true
CapabilityBoundingSet=
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths={STATE} {LOGS}
UMask=0027
[Install]
WantedBy=multi-user.target
''')
    write(units / 'auditbox-server-models.service', f'''[Unit]
Description=AuditBox two CUDA model containers
After=docker.service auditbox-server-gateway.service
Requires=docker.service
Wants=auditbox-server-gateway.service
PartOf=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
Restart=on-failure
RestartSec=10
WorkingDirectory={ROOT}/models-runtime
ExecStart=/usr/bin/python3 {ROOT}/current/deploy/server/start_models.py
ExecStop=/usr/bin/docker compose -f models.compose.yaml stop
TimeoutStartSec=600
TimeoutStopSec=90
[Install]
WantedBy=multi-user.target
''')
    temporary = ROOT / 'current.new'
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(release)
    temporary.replace(current)
    run('systemctl', 'daemon-reload')
    print(json.dumps({'installed': str(release), 'credential_file': str(CONFIG / 'initial-credentials.json'), 'started': False}))


if __name__ == '__main__':
    main()
