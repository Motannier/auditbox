#!/usr/bin/env python3
"""Verify the server model files using the versioned size and SHA-256 manifest."""

import argparse
import hashlib
import json
from pathlib import Path


MANIFEST = Path(__file__).resolve().parents[1] / "models/manifest.json"


def check_models(directory, manifest=MANIFEST):
    models = json.loads(manifest.read_text(encoding="utf-8"))["models"]
    for item in models:
        path = directory / item["file"]
        if not path.is_file():
            raise ValueError(f"Missing model: {item['file']}")
        if path.stat().st_size != item["bytes"]:
            raise ValueError(f"Unexpected size: {item['file']}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != item["sha256"]:
            raise ValueError(f"Unexpected SHA-256: {item['file']}")
        print(f"OK {item['file']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory containing both GGUF model files")
    args = parser.parse_args()
    try:
        check_models(args.directory)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Model check failed: {exc}\n")


if __name__ == "__main__":
    main()
