#!/usr/bin/env bash
set -euo pipefail

ROOT=/opt/auditbox-server
SOURCE=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
address= models= uv_bin=
release=$(date -u +%Y%m%dT%H%M%SZ)

usage() {
  cat <<'EOF'
Usage: sudo ./deploy/server/install.sh --address SERVER_IPV4 --models MODEL_DIRECTORY
                                     [--release VERSION] [--uv /path/to/uv]

First installation only. Requires a built dist/, Python 3.10/3.11 and Linux uv.
Copies a new source release, verifies and copies the two local models, preserves
existing model/runtime configuration, and runs install.py. Does not start or
enable services, pull images, or install Docker/GPU drivers.
EOF
}

while (($#)); do
  case "$1" in
    --address|--models|--release|--uv)
      if (($# < 2)); then usage >&2; exit 2; fi
      case "$1" in
        --address) address=$2 ;;
        --models) models=$2 ;;
        --release) release=$2 ;;
        --uv) uv_bin=$2 ;;
      esac
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
if [[ -z "$address" || -z "$models" ]]; then usage >&2; exit 2; fi
if [[ $(id -u) != 0 || $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo 'Requires root on Linux x86_64.' >&2
  exit 1
fi
umask 022
if [[ -L "$ROOT" ]]; then echo 'Refusing a symlink installation root.' >&2; exit 1; fi
if [[ -e "$ROOT/current" || -L "$ROOT/current" ]]; then
  echo 'An installation already exists; use the documented upgrade procedure.' >&2
  exit 1
fi
if [[ ! -f "$SOURCE/dist/index.html" ]]; then
  echo 'Build the frontend first: ./scripts/quickstart.sh --build-only' >&2
  exit 1
fi
if [[ -e "$ROOT/bin/uv" || -L "$ROOT/bin/uv" ]]; then
  if [[ ! -f "$ROOT/bin/uv" || ! -x "$ROOT/bin/uv" ]]; then
    echo 'Existing private uv is not an executable file; refusing to replace it.' >&2
    exit 1
  fi
  uv_bin=$ROOT/bin/uv
elif [[ -z "$uv_bin" ]]; then
  uv_bin=$(command -v uv || true)
fi
if [[ ! -x "$uv_bin" ]]; then echo 'Provide an executable Linux uv with --uv.' >&2; exit 1; fi

python3 - "$address" "$release" <<'PY'
import ipaddress
import re
import sys

if sys.implementation.name != 'cpython' or sys.version_info[:2] not in {(3, 10), (3, 11)}:
    raise SystemExit('python3 must be CPython 3.10 or 3.11')
try:
    ipaddress.IPv4Address(sys.argv[1])
except ValueError:
    raise SystemExit('--address must be a literal server IPv4 address')
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', sys.argv[2]):
    raise SystemExit('--release must be a name of 1–64 letters, numbers, dots, underscores or hyphens')
PY
"$uv_bin" --version >/dev/null
python3 "$SOURCE/tools/check_models.py" "$models"

python3 - "$SOURCE" "$ROOT" "$models" "$release" "$uv_bin" <<'PY'
import filecmp
import json
from pathlib import Path
import shutil
import sys
import tempfile

source, root, models = (Path(value).resolve() for value in sys.argv[1:4])
release = root / 'releases' / sys.argv[4]
uv = Path(sys.argv[5]).resolve()
if release.exists() or release.is_symlink():
    raise SystemExit('Release already exists: ' + str(release))
weights = json.loads((source / 'models/manifest.json').read_text())['models']
for item in weights:
    target = root / 'models' / item['file']
    if target.is_symlink() or (target.exists() and (
            not target.is_file() or not filecmp.cmp(models / item['file'], target, shallow=False))):
        raise SystemExit('Refusing to overwrite an existing model: ' + str(target))
for path in (root / 'models', root / 'models-runtime', root / 'bin', root / 'releases'):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise SystemExit('Refusing a non-directory or symlink installation path: ' + str(path))
for path in (root / 'models-runtime/.env', root / 'models-runtime/models.compose.yaml'):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise SystemExit('Refusing a non-file or symlink runtime configuration: ' + str(path))

def ignored(directory, names):
    result = []
    for name in names:
        path = Path(directory) / name
        if (name in {'.git', '.venv', 'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache',
                     '.ruff_cache', '.cache', 'logs', 'test-results', 'playwright-report',
                     'auditbox.env', 'initial-credentials.json', '.netrc', '.npmrc', '.DS_Store',
                     'id_rsa', 'id_ed25519', 'id_ecdsa', 'id_dsa'}
                or (Path(directory) in {source / 'box', source / 'agent'} and name == 'data')
                or (name.startswith('.env') and name != '.env.example'
                    and path != source / 'web/.env.production')
                or name.endswith(('.pyc', '.log', '.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite-wal',
                                  '.sqlite-shm', '.sqlite3', '.sqlite3-wal', '.sqlite3-shm', '.gguf',
                                  '.safetensors', '.onnx', '.pt', '.pth', '.pem', '.key', '.ppk',
                                  '.p8', '.p12', '.pfx', '.jks', '.keystore'))):
            result.append(name)
        elif path.is_symlink():
            raise SystemExit('Source symlinks are not supported: ' + str(path))
    return result

(root / 'releases').mkdir(parents=True, exist_ok=True)
staged = Path(tempfile.mkdtemp(prefix='.install-', dir=root / 'releases'))
try:
    for name in ('box', 'agent', 'web', 'deploy', 'docs', 'models', 'tools', 'scripts', '.github', 'dist'):
        if (source / name).is_symlink():
            raise SystemExit('Source symlinks are not supported: ' + str(source / name))
        if (source / name).is_dir():
            shutil.copytree(source / name, staged / name, ignore=ignored)
    for path in source.iterdir():
        if path.is_file() and (path.suffix == '.md' or path.name in {
                'Makefile', 'LICENSE', 'COPYING', 'NOTICE', '.gitignore', '.gitattributes', '.editorconfig'}):
            if path.is_symlink():
                raise SystemExit('Source symlinks are not supported: ' + str(path))
            shutil.copy2(path, staged / path.name)
    for path in staged.rglob('*'):
        path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
    staged.chmod(0o755)
    staged.rename(release)
finally:
    if staged.exists():
        shutil.rmtree(staged)

for name in ('bin', 'models', 'models-runtime'):
    (root / name).mkdir(exist_ok=True)

def copy_new(original, target):
    with tempfile.NamedTemporaryFile(prefix='.' + target.name + '.', dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copy2(original, temporary)
        target.hardlink_to(temporary)
    finally:
        temporary.unlink(missing_ok=True)

if not (root / 'bin/uv').exists():
    copy_new(uv, root / 'bin/uv')
    (root / 'bin/uv').chmod(0o755)
for item in weights:
    target = root / 'models' / item['file']
    if not target.exists():
        copy_new(models / item['file'], target)
for original, name in (('models.compose.yaml', 'models.compose.yaml'), ('models.env.example', '.env')):
    target = root / 'models-runtime' / name
    if not target.exists():
        copy_new(release / 'deploy/server' / original, target)
PY

python3 "$ROOT/releases/$release/deploy/server/install.py" \
  --release "$ROOT/releases/$release" --address "$address"
echo 'Installation prepared. Services were not started or enabled; prepare the configured Docker image before starting them.'
