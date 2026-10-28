#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
MODE=${1:-}
if [[ $# -gt 1 ]] || [[ -n "$MODE" && "$MODE" != --build-only && "$MODE" != --start-only && "$MODE" != --help ]]; then
    printf 'Usage: %s [--build-only|--start-only]\n' "$0" >&2
    exit 2
fi
if [[ "$MODE" == --help ]]; then
    printf 'Usage: %s [--build-only|--start-only]\n' "$0"
    printf 'Default: install locked dependencies, build, configure, and run the core workbench.\n'
    exit 0
fi

if [[ "$MODE" != --start-only ]]; then
    UV=${UV:-uv}
    NPM=${NPM:-npm}
    PYTHON=${PYTHON:-python3}
    PYTHON_VERSION=${PYTHON_VERSION:-3.11}
    for tool in "$NPM" "$UV" "$PYTHON"; do
        command -v "$tool" >/dev/null || { printf 'Required command not found: %s\n' "$tool" >&2; exit 1; }
    done
    (cd -- "$ROOT/web" && "$NPM" ci && "$NPM" run build)
    (cd -- "$ROOT/box" && "$UV" sync --frozen --python "$PYTHON_VERSION")
    "$PYTHON" "$ROOT/tools/init_local_config.py"
fi
[[ "$MODE" != --build-only ]] || exit 0

for file in box/.env dist/index.html; do
    [[ -f "$ROOT/$file" ]] || { printf 'Missing %s; run %s --build-only first.\n' "$file" "$0" >&2; exit 1; }
done
for service in proxy gateway; do
    [[ -x "$ROOT/box/.venv/bin/auditbox-$service" ]] || { printf 'Backend environment is missing; run %s --build-only first.\n' "$0" >&2; exit 1; }
done

pids=''
cleanup() {
    trap - EXIT INT TERM
    for pid in $pids; do kill -TERM -- "-$pid" 2>/dev/null || true; done
    for attempt in 1 2 3; do
        running=false
        for pid in $pids; do
            if kill -0 -- "-$pid" 2>/dev/null; then running=true; fi
        done
        [[ "$running" == true ]] || break
        sleep 1
    done
    for pid in $pids; do
        kill -KILL -- "-$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Separate job groups let shutdown also reach each service's own children.
set -m
for service in proxy gateway; do
    (cd -- "$ROOT/box" && exec ".venv/bin/auditbox-$service") &
    pids="$pids $!"
done
set +m
printf 'Core services starting. Default URL: http://127.0.0.1:18080\n'
printf 'Credentials: %s/box/.env (existing settings are preserved). Ctrl+C stops both services.\n' "$ROOT"
while true; do
    for pid in $pids; do
        if ! kill -0 "$pid" 2>/dev/null; then
            status=0
            wait "$pid" || status=$?
            exit "$status"
        fi
    done
    sleep 1
done
