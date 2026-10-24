"""Quality gating and optional OmniSR adapter. SR never runs without an explicit model command."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageStat


@dataclass
class Quality:
    width: int
    height: int
    min_dimension: int
    sharpness: float
    contrast: float
    low_resolution: bool
    sr_applied: bool = False
    sr_latency_sec: float = 0.0
    notes: list[str] | None = None


def assess(image: Image.Image, min_dimension: int = 1400, sharpness_threshold: float = 35.0,
           contrast_threshold: float = 24.0) -> Quality:
    gray = image.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    sharpness = float(ImageStat.Stat(edges).var[0])
    contrast = float(ImageStat.Stat(gray).stddev[0])
    low = min(image.size) < min_dimension or sharpness < sharpness_threshold or contrast < contrast_threshold
    return Quality(image.width, image.height, min(image.size), sharpness, contrast, low, notes=[])


# config file cache: the SR yaml is re-read per page without this (mmaps + parse × pages)
_SR_CFG_CACHE: dict[str, tuple[float, dict]] = {}


def _sr_config(config_path: str | None) -> dict:
    cfg = {"enabled": False, "min_dimension": 1400, "sharpness_threshold": 35,
           "contrast_threshold": 24, "scale": 2, "command": ""}
    if not config_path:
        return cfg
    p = Path(config_path)
    if not p.exists():
        return cfg
    mtime = p.stat().st_mtime
    hit = _SR_CFG_CACHE.get(str(p))
    if hit and hit[0] == mtime:
        return dict(hit[1])
    loaded = dict(cfg)
    try:
        import yaml
        loaded.update(yaml.safe_load(p.read_text()) or {})
    except ImportError:
        loaded.update(json.loads(p.read_text()))
    _SR_CFG_CACHE[str(p)] = (mtime, loaded)
    return dict(loaded)


def maybe_super_resolve(path: str, *, enabled: bool | None = None, force: bool = False,
                        config_path: str | None = None) -> tuple[str, dict]:
    """Returns an image path and auditable quality metadata. Command uses {input}/{output} placeholders."""
    image = Image.open(path).convert("RGB")
    cfg = _sr_config(config_path)
    if enabled is not None:
        cfg["enabled"] = enabled
    q = assess(image, int(cfg["min_dimension"]), float(cfg["sharpness_threshold"]), float(cfg["contrast_threshold"]))
    meta = asdict(q)
    if not (force or cfg["enabled"] and q.low_resolution):
        return path, meta
    command = os.getenv("OMNISR_COMMAND", cfg.get("command", "")).strip()
    if not command:
        meta["notes"].append("quality gate triggered; no trained OmniSR command configured, original retained")
        return path, meta
    with tempfile.TemporaryDirectory(prefix="invoice-omnisr-") as td:
        out = str(Path(td) / "sr.png")
        args = [a.replace("{input}", path).replace("{output}", out).replace("{scale}", str(cfg["scale"]))
                for a in shlex.split(command)]
        start = time.monotonic()
        proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
        meta["sr_latency_sec"] = round(time.monotonic() - start, 4)
        if proc.returncode or not Path(out).exists():
            meta["notes"].append("OmniSR command failed; original retained: " + (proc.stderr[-300:] or str(proc.returncode)))
            return path, meta
        # Keep SR output alive for the caller by copying it into an adjacent temporary file.
        target = str(Path(td).parent / f"invoice-sr-{os.getpid()}-{time.time_ns()}.png")
        Image.open(out).save(target)
        q2 = assess(Image.open(target), int(cfg["min_dimension"]), float(cfg["sharpness_threshold"]), float(cfg["contrast_threshold"]))
        meta["sr_applied"] = True
        meta["after"] = asdict(q2)
        return target, meta
