"""Remove only expired, marked benchmark run directories after resolving their safe root."""

import argparse
import json
import shutil
import time
from backend.config import ROOT, settings


def expired_runs(root=None, now=None):
    root = (root or ROOT / "data" / "benchmarks" / "concurrency").resolve()
    now = time.time() if now is None else now
    if not root.exists():
        return []
    result = []
    for manifest in root.rglob("manifest.json"):
        folder = manifest.parent.resolve()
        if (
            folder == root
            or not folder.is_relative_to(root)
            or folder.is_relative_to(settings.storage_dir.resolve())
        ):
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if (
                data.get("benchmark") in ("sumx-concurrency-v1", "sumx-modal-sequential-v1")
                and 0 < data.get("expires_at", 0) < now
            ):
                result.append(folder)
        except (ValueError, OSError, TypeError):
            continue
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete-expired", action="store_true")
    args = parser.parse_args()
    for folder in expired_runs():
        print(folder)
        if args.delete_expired:
            shutil.rmtree(folder)


if __name__ == "__main__":
    main()
