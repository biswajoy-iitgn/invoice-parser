#!/usr/bin/env python3
"""Thin wrapper around the Lightning AI SDK for the retraining runs.

  .venv/bin/python scripts/lightning_ops.py status
  .venv/bin/python scripts/lightning_ops.py start --machine CPU          # setup / uploads, no GPU billing
  .venv/bin/python scripts/lightning_ops.py switch --machine L40S        # GPU for training jobs
  .venv/bin/python scripts/lightning_ops.py run "nvidia-smi"
  .venv/bin/python scripts/lightning_ops.py upload LOCAL REMOTE
  .venv/bin/python scripts/lightning_ops.py download REMOTE LOCAL
  .venv/bin/python scripts/lightning_ops.py stop                          # always stop when idle

Studio: "invoice-retrain" in the teamspace from ~/.lightning/config.yaml (deploy-model-project).
"""
from __future__ import annotations

import argparse
import os
import sys

STUDIO = os.environ.get("LIGHTNING_STUDIO_NAME", "invoice-retrain")
TEAMSPACE = os.environ.get("LIGHTNING_TEAMSPACE", "deploy-model-project")
USER = os.environ.get("LIGHTNING_USER", "biswajoyb")


def studio():
    from lightning_sdk import Studio

    return Studio(name=STUDIO, teamspace=TEAMSPACE, user=USER, create_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "start", "switch", "run", "upload", "download", "stop"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--machine", default="CPU")
    a = ap.parse_args()
    s = studio()
    if a.cmd == "status":
        print({"name": s.name, "status": str(s.status), "machine": str(s.machine)})
    elif a.cmd == "start":
        s.start(a.machine)
        print("started", s.status, s.machine)
    elif a.cmd == "switch":
        s.switch_machine(a.machine)
        print("switched", s.status, s.machine)
    elif a.cmd == "run":
        out, code = s.run_with_exit_code(" ".join(a.args))
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
        sys.exit(code)
    elif a.cmd == "upload":
        src, dst = a.args
        (s.upload_folder if os.path.isdir(src) else s.upload_file)(src, dst)
        print("uploaded", src, "->", dst)
    elif a.cmd == "download":
        src, dst = a.args
        try:
            s.download_folder(src, dst)
        except Exception:
            s.download_file(src, dst)
        print("downloaded", src, "->", dst)
    elif a.cmd == "stop":
        s.stop()
        print("stopped", s.status)


if __name__ == "__main__":
    main()
