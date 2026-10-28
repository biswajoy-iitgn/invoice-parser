"""Writes UI/src/serverProfiles.json: the service's profiles and model catalog, so the UI shows them offline too.
Run from the repo root after editing configs/profiles/*.yaml or configs/models.yaml:  .venv/bin/python scripts/export_profiles.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor import profiles  # noqa: E402

out = ROOT / "UI" / "src" / "serverProfiles.json"
out.write_text(json.dumps({"profiles": [profiles.describe(p) for p in profiles.load_all().values()], "models": profiles.catalog()}, indent=1, ensure_ascii=False) + "\n")
print(f"wrote {out.relative_to(ROOT)}")
