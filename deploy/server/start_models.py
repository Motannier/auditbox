#!/usr/bin/env python3
"""Start Linux review first, then the assistant, waiting for actual readiness."""
import json
import subprocess
import time
import urllib.error
import urllib.request


def main():
    for service, port in (('linux-audit', 18082), ('assistant', 18081)):
        subprocess.run(['/usr/bin/docker', 'compose', '-f', 'models.compose.yaml',
                        'up', '-d', '--no-build', '--pull', 'never', service], check=True)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3) as response:
                    if json.load(response).get('status') == 'ok':
                        print(service + ' ready', flush=True)
                        break
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(2)
        else:
            raise SystemExit(service + ' did not become ready; inspect its container logs')


if __name__ == '__main__':
    main()
