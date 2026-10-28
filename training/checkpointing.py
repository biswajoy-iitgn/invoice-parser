"""Reusable atomic checkpoint/resume and hard wall-clock budget guard."""
from __future__ import annotations
import os, random, tempfile, time
from pathlib import Path

def save_checkpoint(path, *, model, optimizer=None, scheduler=None, scaler=None, epoch=0, step=0, config=None, dataset_version="", best_metric=None):
 import torch
 payload={"model":model.state_dict(),"optimizer":optimizer.state_dict() if optimizer else None,"scheduler":scheduler.state_dict() if scheduler else None,"scaler":scaler.state_dict() if scaler else None,"epoch":epoch,"step":step,"config":config or {},"dataset_version":dataset_version,"best_metric":best_metric,"python_rng":random.getstate(),"torch_rng":torch.get_rng_state(),"cuda_rng":torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None}
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(dir=p.parent,prefix=p.name);os.close(fd)
 try: torch.save(payload,tmp);os.replace(tmp,p)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def load_checkpoint(path, *, model, optimizer=None, scheduler=None, scaler=None, map_location="cpu"):
 import torch
 data=torch.load(path,map_location=map_location,weights_only=False);model.load_state_dict(data["model"])
 if optimizer and data.get("optimizer"):optimizer.load_state_dict(data["optimizer"])
 if scheduler and data.get("scheduler"):scheduler.load_state_dict(data["scheduler"])
 if scaler and data.get("scaler"):scaler.load_state_dict(data["scaler"])
 try:
  random.setstate(data["python_rng"])
 except Exception as exc: print(f"WARNING: python RNG restore skipped: {exc}")
 try:
  torch.set_rng_state(data["torch_rng"])
 except Exception as exc: print(f"WARNING: torch RNG restore skipped: {exc}")
 if torch.cuda.is_available() and data.get("cuda_rng"):
  try: torch.cuda.set_rng_state_all(data["cuda_rng"])
  except Exception as exc: print(f"WARNING: cuda RNG restore skipped: {exc}")
 return data

class Budget:
 def __init__(self,minutes=85):self.end=time.monotonic()+minutes*60
 def expired(self):return time.monotonic()>=self.end
