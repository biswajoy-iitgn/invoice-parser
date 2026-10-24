"""SUPERSEDED (licensing): OmniSR removed in favor of SwinIR
(see training/train_swinir.py, invoice_extractor/sr_swinir.py). Kept for reference only.

Factory + inference entry points for the OFFICIAL OmniSR model.

Upstream: https://github.com/Francis0625/Omni-SR (CVPR 2023, no LICENSE file
found in the repo as of 2026-10-01; code and weights are used locally only and
are NOT redistributed with this project).

Setup (once per machine):
  git clone --depth 1 https://github.com/Francis0625/Omni-SR.git /opt/omni-sr-ref
  export OMNISR_REF=/opt/omni-sr-ref
  # optional pretrained weights (DF2K x2):
  #   https://drive.google.com/file/d/12EvHRof0-kA2Wt_BzfFJBK1J0jbzfz-4/view?usp=sharing
  #   -> $OMNISR_REF/train_logs/OmniSR_X2_DF2K/...

Training:
  OMNISR_FACTORY=training.omnisr_factory:build_official_omnisr \\
    .venv/bin/python training/train_omnisr.py --config configs/omnisr.yaml

Inference (used by quality gating via OMNISR_COMMAND):
  OMNISR_COMMAND='.venv/bin/python scripts/super_resolve.py --input {input} --output {output} --scale {scale}'
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _ref_dir() -> Path:
    d = Path(os.environ.get("OMNISR_REF", "/tmp/omni-sr-ref"))
    if not (d / "components" / "OmniSR.py").exists():
        raise RuntimeError(
            f"Official OmniSR reference not found at {d}. Clone it with: "
            "git clone --depth 1 https://github.com/Francis0625/Omni-SR.git "
            "<dir> && export OMNISR_REF=<dir>"
        )
    return d


def build_official_omnisr(scale: int = 2, pretrained: str | None = None):
    """Construct the official OmniSR nn.Module (never a substitute network)."""
    import torch

    ref = _ref_dir()
    sys.path.insert(0, str(ref))
    try:
        from components.OmniSR import OmniSR
    finally:
        sys.path.remove(str(ref))
    model = OmniSR(
        num_in_ch=3, num_out_ch=3, num_feat=64,
        res_num=5, upsampling=int(scale), bias=True,
        block_num=1, block_script_name="OSA", block_class_name="OSA_Block",
        window_size=8, pe=True, ffn_bias=True,
    )
    ckpt = pretrained or os.environ.get("OMNISR_PRETRAINED", "")
    if ckpt:
        state = torch.load(ckpt, map_location="cpu", weights_only=True)
        # Official checkpoints nest params under 'params' or 'model'.
        for key in ("params", "model", "state_dict"):
            if isinstance(state, dict) and key in state:
                state = state[key]
                break
        own = model.state_dict()
        state = {k: v for k, v in state.items() if k in own}
        model.load_state_dict(state, strict=True)
    n = sum(p.numel() for p in model.parameters())
    assert 700_000 < n < 900_000, f"unexpected OmniSR size: {n}"
    return model
