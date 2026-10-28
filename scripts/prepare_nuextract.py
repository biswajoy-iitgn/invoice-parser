#!/usr/bin/env python3
"""A servable copy of NuExtract3 for the model pool: work/models/NuExtract3-mlx-nvfp4, the downloaded files symlinked (no
second copy of the weights) and its chat template patched in one place.

NuExtract3 takes its JSON template and instructions as chat-template arguments; mlx_vlm's server passes only thinking options
to templates. The patch reads them from a first system message instead:
    【nuextract_template】{...json...}【nuextract_instructions】...      (pipeline.call_reader builds it)
so the model sees exactly the prompt its card describes. Run once after downloading numind/NuExtract3-mlx-nvfp4.
"""
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
PATCH = """{#- model pool adapter (scripts/prepare_nuextract.py): template and instructions from a marked first system message -#}
{%- if messages and messages[0].role == 'system' and messages[0].content is string and '【nuextract_template】' in messages[0].content -%}
    {%- set _parts = messages[0].content.split('【nuextract_template】')[1].split('【nuextract_instructions】') -%}
    {%- set template = _parts[0] -%}
    {%- if _parts|length > 1 and _parts[1] -%}{%- set instructions = _parts[1] -%}{%- endif -%}
    {%- set messages = messages[1:] -%}
{%- endif -%}
"""


def main() -> None:
    src = Path(snapshot_download("numind/NuExtract3-mlx-nvfp4", local_files_only=True))
    dst = ROOT / "work" / "models" / "NuExtract3-mlx-nvfp4"
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        link = dst / f.name
        if f.name == "chat_template.jinja":
            link.unlink(missing_ok=True)
            link.write_text(PATCH + f.read_text())
        elif not link.exists():
            link.symlink_to(f.resolve())
    print(f"ready: {dst}")


if __name__ == "__main__":
    main()
