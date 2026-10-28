#!/usr/bin/env python3
"""Live training dashboard (read-only) for the retraining run.

  .venv/bin/python scripts/training_dashboard.py          # http://127.0.0.1:8767

Sources:
  * Mac: semantic encoder, LAYA v2, SwinIR logs (re-read every second), the LAYA step counter
    from checkpoints/laya-invoice-v2/latest.pt (metadata only), and process liveness / CPU.
  * JarvisLabs A100 (`jl exec`, every DASH_REMOTE_EVERY s): autopilot status, per-stage logs,
    exact OCR result count, reader loss curve, nvidia-smi. Spend and balance every 60 s.
The page receives every change over Server-Sent Events. Nothing here starts, stops or pauses jobs.
"""
from __future__ import annotations

import json
import os
import pickle
import re
import shutil
import subprocess
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
MACHINE = os.environ.get("JL_MACHINE", "524820")   # the ID changes when a paused instance is resumed
RATE_INR_H = float(os.environ.get("JL_RATE_INR_H", "141"))
REMOTE_EVERY = float(os.environ.get("DASH_REMOTE_EVERY", "10"))
PORT = int(os.environ.get("DASH_PORT", "8767"))
JL = shutil.which("jl") or str(Path.home() / ".local/bin/jl")
OBS_FILE = WORK / ".dashboard_obs.json"

STATE: dict = {"remote": {}, "account": {}}
VERSION = 0
COND = threading.Condition()


def publish(**kv) -> None:
    global VERSION
    with COND:
        changed = False
        for k, v in kv.items():
            if STATE.get(k) != v:
                STATE[k] = v
                changed = True
        if changed:
            VERSION += 1
            COND.notify_all()


# ---------------------------------------------------------------- helpers

def tail_lines(path: Path, nbytes: int = 65536) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [ln for ln in data.replace("\r", "\n").split("\n") if ln.strip()]


def mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def processes() -> list[dict]:
    try:
        out = subprocess.run(["ps", "-axo", "pid=,lstart=,%cpu=,command="], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return []
    rows = []
    for ln in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\w{3}\s+\w{3}\s+\d+\s+[\d:]+\s+\d{4})\s+([\d.]+)\s+(.*)$", ln)
        if m:
            try:
                start = time.mktime(time.strptime(m.group(2), "%a %b %d %H:%M:%S %Y"))
            except ValueError:
                start = None
            rows.append({"pid": int(m.group(1)), "start": start, "cpu": float(m.group(3)), "cmd": m.group(4)})
    return rows


def find_proc(procs: list[dict], needle: str, python: bool = True) -> dict | None:
    for p in procs:
        if needle in p["cmd"] and (not python or "ython" in p["cmd"]) and "grep" not in p["cmd"]:
            return p
    return None


def arg(cmd: str, flag: str, default):
    m = re.search(rf"{re.escape(flag)}[ =](\S+)", cmd or "")
    return type(default)(m.group(1)) if m else default


def error_excerpt(lines: list[str]) -> str | None:
    idx = [i for i, ln in enumerate(lines) if ln.startswith("Traceback")]
    if not idx:
        return None
    return "\n".join(lines[idx[-1]:][-14:])


class _Any:
    def __init__(self, *a, **k): pass
    def __setstate__(self, s): pass


class _MetaUnpickler(pickle.Unpickler):
    """Reads a torch zip checkpoint's pickle without materializing any tensor."""
    def find_class(self, module, name):
        if module in ("builtins", "collections", "_codecs", "copyreg"):
            return super().find_class(module, name)
        return _Any

    def persistent_load(self, pid):
        return None


def ckpt_meta(path: Path) -> dict | None:
    try:
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith("data.pkl"))
            with z.open(name) as f:
                d = _MetaUnpickler(f).load()
        return {k: d.get(k) for k in ("epoch", "step", "best_metric")} if isinstance(d, dict) else None
    except Exception:
        return None   # being rewritten right now; keep the previous reading


try:
    OBS: dict = json.loads(OBS_FILE.read_text())
except Exception:
    OBS = {}


SEEN_ONCE: set[str] = set()   # jobs parsed at least once by this process


def stamp(key: str, is_last: bool, log_mtime: float | None) -> float | None:
    """Wall time a log line appeared: seen live -> now; newest line at first sight -> log mtime."""
    if key not in OBS:
        OBS[key] = time.time() if key.split(":")[0] in SEEN_ONCE else (log_mtime if is_last else None)
        try:
            OBS_FILE.write_text(json.dumps(OBS))
        except OSError:
            pass
    return OBS[key]


# ---------------------------------------------------------------- local jobs

def job_encoder(procs) -> dict:
    log = WORK / "train_encoder.log"
    lines, lm = tail_lines(log), mtime(log)
    p = find_proc(procs, "training/train_semantic_encoder.py")
    head, epochs, done = None, [], None
    for ln in lines:
        s = ln.strip()
        if s.startswith("{"):
            try:
                d = json.loads(s)
            except ValueError:
                continue
            if "train" in d and "val" in d:
                head = d
            elif "epoch" in d:
                epochs.append(d)
        elif s.startswith("ENCODER_DONE"):
            try:
                done = json.loads(s.split(" ", 1)[1])
            except (IndexError, ValueError):
                done = {}
    for i, e in enumerate(epochs):
        e["at"] = stamp(f"encoder:epoch:{e['epoch']}", i == len(epochs) - 1, lm)
    SEEN_ONCE.add("encoder")
    cmd = p["cmd"] if p else ""
    max_ep, batch = arg(cmd, "--epochs", 3), arg(cmd, "--batch", 256)
    err = error_excerpt(lines)
    state = "done" if done is not None else "failed" if err else "running" if p else ("stopped" if lines else "queued")
    return {"state": state, "pid": p and p["pid"], "cpu": p and p["cpu"], "started": p and p["start"],
            "log_at": lm, "head": head, "epochs": epochs, "max_epochs": max_ep,
            "steps_per_epoch": (head["train"] // batch + 1) if head else None, "done": done, "error": err,
            "tail": lines[-6:], "output": "checkpoints/semantic-encoder"}


LAYA_CKPT: dict = {"mtime": None, "meta": None, "history": []}


def job_laya(procs) -> dict:
    log = WORK / "train_laya_v2.log"
    lines, lm = tail_lines(log), mtime(log)
    p = find_proc(procs, "training/train_laya.py")
    cmd = p["cmd"] if p else ""
    out = ROOT / arg(cmd, "--output", "checkpoints/laya-invoice-v2")
    ck = out / "latest.pt"
    cm = mtime(ck)
    if cm and cm != LAYA_CKPT["mtime"]:
        meta = ckpt_meta(ck)
        if meta:
            LAYA_CKPT.update(mtime=cm, meta=meta)
            h = LAYA_CKPT["history"]
            if not h or h[-1][1] != meta["step"]:
                h.append((cm, meta["step"]))
                del h[:-20]
    head = next((re.search(r"decisions train=(\d+) val=(\d+) batches=(\d+) device=(\w+)", ln) for ln in lines
                 if ln.startswith("decisions ")), None)
    epochs = []
    for ln in lines:
        m = re.match(r"epoch=(\d+) train_loss=([\d.]+) val_loss=([\d.]+) val_acc=([\d.]+)\s*(BEST)?", ln)
        if m:
            epochs.append({"epoch": int(m.group(1)), "train_loss": float(m.group(2)), "val_loss": float(m.group(3)),
                           "val_acc": float(m.group(4)), "best": bool(m.group(5))})
    exported = next((ln.split(" ", 1)[1] for ln in lines if ln.startswith("exported ")), None)
    stopped = next((ln for ln in lines if ln.startswith("stop epoch=")), None)
    batches = int(head.group(3)) if head else None
    max_ep = arg(cmd, "--epochs", 3)
    # a resumed run restarts its epoch from the first batch but keeps counting steps from the checkpoint
    res = next((m for m in (re.match(r"resumed epoch=(\d+) step=(\d+)", ln) for ln in reversed(lines)) if m), None)
    total = (int(res.group(2)) + (max_ep - int(res.group(1))) * batches) if res and batches else (batches * max_ep if batches else None)
    meta = LAYA_CKPT["meta"]
    # live step counter: "step=N loss=x" lines (every --log-every steps) of the current run
    run = lines[max((i for i, ln in enumerate(lines) if ln.startswith("decisions ")), default=0):]
    logged = [(int(m.group(1)), float(m.group(2))) for m in (re.match(r"step=(\d+) loss=([\d.]+)", ln) for ln in run) if m]
    step, step_at, loss = (meta["step"], LAYA_CKPT["mtime"], None) if meta else (None, None, None)
    if logged and (step is None or logged[-1][0] >= step):
        step, step_at, loss = logged[-1][0], lm, logged[-1][1]
    obs = LAYA_CKPT.setdefault("obs", [])
    if p and step is not None and step_at and (not obs or obs[-1][1] != step):
        if obs and (step < obs[-1][1] or p["pid"] != LAYA_CKPT.get("pid")):
            obs.clear()
        LAYA_CKPT["pid"] = p["pid"]
        obs.append((step_at, step))
        del obs[:-30]
    rate, rate_kind = None, None
    if len(obs) >= 2 and obs[-1][0] > obs[0][0]:
        rate, rate_kind = (obs[-1][1] - obs[0][1]) / (obs[-1][0] - obs[0][0]), f"measured over the last {obs[-1][1] - obs[0][1]} steps"
    err = error_excerpt(lines)
    state = ("done" if exported else "failed" if err else "stopped" if stopped else "running" if p
             else ("stopped" if lines else "queued"))
    return {"state": state, "pid": p and p["pid"], "cpu": p and p["cpu"], "started": p and p["start"],
            "log_at": lm, "train": head and int(head.group(1)), "val": head and int(head.group(2)),
            "batches": batches, "device": head and head.group(4), "max_epochs": max_ep,
            "total_steps": total, "resumed_at": res and int(res.group(2)), "step": step, "loss": loss,
            "ckpt_epoch": meta and meta["epoch"], "ckpt_at": step_at, "saved_at": LAYA_CKPT["mtime"], "rate": rate,
            "rate_kind": rate_kind,
            "epochs": epochs, "exported": exported, "stopped": stopped, "error": err, "tail": lines[-6:],
            "output": str(out.relative_to(ROOT)) + "/laya-invoice-ft"}


def job_swinir(procs) -> dict:
    p = find_proc(procs, "train_swinir.py")
    logs = sorted(WORK.glob("*swinir*.log"), key=lambda q: q.stat().st_mtime)
    log = logs[-1] if logs else None
    lines = tail_lines(log) if log else []
    epochs, done = [], None
    for ln in lines:
        s = ln.strip()
        if s.startswith("{"):
            try:
                d = json.loads(s)
                if "epoch" in d:
                    epochs.append(d)
            except ValueError:
                pass
        elif s.startswith("SWINIR_DONE"):
            try:
                done = json.loads(s.split(" ", 1)[1])
            except (IndexError, ValueError):
                done = {}
    err = error_excerpt(lines)
    state = "done" if done is not None else "failed" if err else "running" if p else ("stopped" if lines else "queued")
    live = next((m for m in (re.match(r"step=(\d+)/(\d+) epoch=(\d+) l1=([\d.]+)", ln) for ln in reversed(lines)) if m), None)
    return {"state": state, "pid": p and p["pid"], "cpu": p and p["cpu"], "started": p and p["start"],
            "live": live and {"step": int(live.group(1)), "steps": int(live.group(2)), "epoch": int(live.group(3)),
                              "l1": float(live.group(4))},
            "log": log and log.name, "log_at": log and mtime(log), "epochs": epochs,
            "max_epochs": arg(p["cmd"] if p else "", "--epochs", 12), "done": done, "error": err, "tail": lines[-6:]}


def job_watch(procs) -> dict:
    p = next((q for q in procs if "work/jl_watch" in q["cmd"] and "bash" in q["cmd"] and "caffeinate" not in q["cmd"]), None)
    lines = tail_lines(WORK / "jl_watch.log")
    dl = next((float(m.group(1)) for m in (re.search(r"deadline=(\d+)", ln) for ln in reversed(lines)) if m), None)
    paused = any("PAUSED" in ln for ln in lines)
    final = next((m.group(1) for m in (re.search(r"status=(\w+)", ln) for ln in reversed(lines)) if m), None)
    return {"final": final, "alive": bool(p), "started": p and p["start"], "deadline": dl or (p and p["start"] and p["start"] + 3 * 3600), "machine": MACHINE,
            "paused": paused, "tail": lines[-4:]}


def local_loop() -> None:
    while True:
        procs = processes()
        try:
            publish(encoder=job_encoder(procs), laya=job_laya(procs), swinir=job_swinir(procs), watch=job_watch(procs),
                    local_at=time.time())
        except Exception as e:   # never let the collector die
            publish(local_error=repr(e))
        time.sleep(1)


# ---------------------------------------------------------------- A100

REMOTE_SH = r"""echo @@status; cat /home/autopilot.status 2>/dev/null
echo @@setup; cat /home/logs_setup.log /home/logs_setup2.log 2>/dev/null | grep -c SETUP_DONE
for s in unpack ocr readerdata reader export; do echo "@@log $s"; if [ -f /home/logs/$s.log ]; then stat -c %Y /home/logs/$s.log; tail -c 12000 /home/logs/$s.log | tr "\r" "\n" | grep -v "^[[:space:]]*$" | tail -80; fi; done
echo @@readermeta; grep -m1 "steps/epoch=" /home/logs/reader.log 2>/dev/null; grep -m1 train_examples /home/logs/reader.log 2>/dev/null
echo @@ocrcount; wc -l < /home/data/dataset_v2/ocr_requests.jsonl 2>/dev/null; wc -l < /home/data/dataset_v2/ocr_results.jsonl 2>/dev/null
echo @@gpu; nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw --format=csv,noheader,nounits
echo @@curve; cat /home/invoice_parsing/checkpoints/reader-qwen3vl-8b/loss_curve.json 2>/dev/null; echo
echo @@now; date +%s
echo @@end"""

STAGES = [("setup", "Setup", "SETUP_DONE"), ("unpack", "Unpack", "UNPACK_DONE"), ("ocr", "PP-OCRv6 on scans", "OCR_DONE"),
          ("readerdata", "Reader data", "READERDATA_DONE"), ("reader", "Qwen3-VL-8B LoRA", "READER_TRAIN_DONE"),
          ("export", "LoRA → GGUF", "EXPORT_DONE")]


def hms(s: str) -> float | None:
    try:
        parts = [float(x) for x in s.split(":")]
    except ValueError:
        return None
    t = 0.0
    for x in parts:
        t = t * 60 + x
    return t


def parse_remote(out: str, polled: float) -> dict:
    sec, cur = {}, None
    for ln in out.splitlines():
        if ln.startswith("@@"):
            cur = ln[2:].strip()
            sec[cur] = []
        elif cur:
            sec[cur].append(ln)
    if "end" not in sec:
        raise RuntimeError("incomplete response from the instance")
    try:
        off = polled - float(sec["now"][0])
    except (KeyError, IndexError, ValueError):
        off = 0.0
    status = next((s.strip() for s in sec.get("status", []) if s.strip()), "—")
    stages = []
    for key, label, marker in STAGES:
        st = {"key": key, "label": label}
        if key == "setup":
            st["started"] = True
            st["done"] = any(s.strip() not in ("", "0") for s in sec.get("setup", []))
            st["lines"] = []
        else:
            body = sec.get(f"log {key}", [])
            st["started"] = bool(body)
            st["log_at"] = (float(body[0]) + off) if body and body[0].strip().isdigit() else None
            st["lines"] = body[1:] if body else []
            st["done"] = any(marker in ln for ln in st["lines"])
            st["error"] = error_excerpt(st["lines"])
        stages.append(st)
    last = max((i for i, s in enumerate(stages) if s["started"]), default=-1)
    for i, s in enumerate(stages):
        if s["done"] or status == "ALL_DONE":
            s["state"] = "done"
        elif s["started"]:
            s["state"] = ("running" if status == "RUNNING" else "failed" if status == "FAILED" else "stopped") \
                if i == last else "failed"
        else:
            s["state"] = "pending"
        s["tail"] = s.pop("lines")[-14:] if s["state"] != "pending" else []

    by = {s["key"]: s for s in stages}
    # OCR: exact count from the results file, rate/eta from the stage log
    oc = [x.strip() for x in sec.get("ocrcount", []) if x.strip().isdigit()]
    ocr = by["ocr"]
    if oc:
        ocr["progress"] = {"done": int(oc[1]) if len(oc) > 1 else 0, "total": int(oc[0]), "unit": "pages"}
    text = "\n".join(ocr["tail"])
    m = list(re.finditer(r"(\d+)/(\d+) pages\s+([\d.]+) pages/s\s+eta ([\d.]+) min", text))
    if m and ocr.get("progress") is not None:
        ocr["progress"]["rate"] = f"{m[-1].group(3)} pages/s"
        if ocr["state"] == "running" and ocr.get("log_at"):
            ocr["progress"]["eta_at"] = ocr["log_at"] + float(m[-1].group(4)) * 60
    d = re.search(r"OCR_DONE (\d+) pages in ([\d.]+) min", text)
    if d:
        ocr["summary"] = f"{d.group(1)} pages in {d.group(2)} min"

    # Reader LoRA: tqdm step counter + loss curve written by the training callback
    rd = by["reader"]
    rtext = "\n".join(rd["tail"])
    full = rd["tail"]
    meta_lines = sec.get("readermeta", [])   # grepped from the whole log: the tail scrolls past them
    total = next((int(x.group(1)) for x in (re.search(r"steps/epoch=(\d+)", ln) for ln in meta_lines + full) if x), None)
    head = None
    for ln in meta_lines + full:
        if ln.startswith("{") and "train_examples" in ln:
            try:
                head = json.loads(ln)
            except ValueError:
                pass
    bars = list(re.finditer(r"(\d+)/(\d+) \[([\d:]+)<([\d:?]+)", rtext))
    curve = []
    try:
        curve = json.loads("\n".join(sec.get("curve", [])).strip() or "[]")
    except ValueError:
        pass
    rd["head"] = head
    rd["curve"] = [{k: c[k] for k in ("step", "loss", "eval_loss", "learning_rate", "epoch") if k in c} for c in curve]
    step = max((c["step"] for c in curve), default=0)
    train_bars = [b for b in bars if total and int(b.group(2)) == total]
    if train_bars:
        b = train_bars[-1]
        step = max(step, int(b.group(1)))
        rem = hms(b.group(4))
        if rd["state"] == "running" and rem is not None and rd.get("log_at"):
            rd["eta_at"] = rd["log_at"] + rem
    if total:
        rd["progress"] = {"done": step, "total": total, "unit": "steps"}
    rd["evaluating"] = bool(bars and total and int(bars[-1].group(2)) != total
                            and int(bars[-1].group(1)) < int(bars[-1].group(2)) and step > 0)
    done = next((ln for ln in full if ln.startswith("READER_TRAIN_DONE")), None)
    if done:
        try:
            s = json.loads(done.split(" ", 1)[1])
            rd["summary"] = f"best eval loss {s.get('best_eval_loss'):.4f} at step {s.get('global_step')}"
        except Exception:
            rd["summary"] = "finished"

    gpu = None
    g = next((x for x in sec.get("gpu", []) if x.strip()), None)
    if g:
        try:
            u, mu, mt, tc, pw = (float(x) for x in g.split(","))
            gpu = {"util": u, "mem_used": mu, "mem_total": mt, "temp": tc, "power": pw}
        except ValueError:
            pass
    return {"status": status, "stages": stages, "gpu": gpu}


def offline_dump() -> str:
    """Same section format as REMOTE_SH, from the copies jl_watch.sh downloaded before pausing."""
    d = WORK / "jl_logs" / "logs"
    st = "—"
    for ln in tail_lines(WORK / "jl_watch.log"):
        m = re.search(r"status=(\w+)", ln)
        if m:
            st = m.group(1)
    out = ["@@status", st, "@@setup", "1" if any(d.glob("*.log")) else "0"]
    for key, _, _ in STAGES[1:]:
        out.append(f"@@log {key}")
        f = d / f"{key}.log"
        if f.exists():
            out.append(str(int(f.stat().st_mtime)))
            out += tail_lines(f, 12000)[-80:]
    text = "\n".join(tail_lines(d / "ocr.log"))
    req, fin = re.search(r"(\d+) requests", text), re.search(r"OCR_DONE (\d+)", text)
    prog = re.findall(r"(\d+)/\d+ pages", text)
    out.append("@@readermeta")
    out += [ln for ln in tail_lines(d / "reader.log", 4_000_000) if "steps/epoch=" in ln or "train_examples" in ln][:2]
    out.append("@@ocrcount")
    if req:
        out += [req.group(1), fin.group(1) if fin else (prog[-1] if prog else "0")]
    out += ["@@gpu", "@@curve"]
    c = ROOT / "checkpoints/reader-qwen3vl-8b/loss_curve.json"
    if c.exists():
        out.append(c.read_text())
    out += ["@@now", str(int(time.time())), "@@end"]
    return "\n".join(out)


# jl is its own Python app: don't leak this venv's launcher variable or forced ANSI colors into it
JL_ENV = {k: v for k, v in os.environ.items() if k not in ("__PYVENV_LAUNCHER__", "FORCE_COLOR", "PYTHONHOME", "PYTHONPATH")}
JL_ENV["NO_COLOR"] = "1"


def jl(*args: str, timeout: float = 60) -> str:
    p = subprocess.run([JL, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                       env=JL_ENV)
    if p.returncode != 0 and not p.stdout.strip():
        raise RuntimeError((p.stderr or f"jl exited {p.returncode}").strip()[-300:])
    return p.stdout


def remote_loop() -> None:
    while not STATE.get("account"):
        time.sleep(0.5)
    while True:
        acct = STATE.get("account") or {}
        watch = STATE.get("watch") or {}
        r = dict(STATE.get("remote") or {})
        polled = time.time()
        if acct.get("instance_status") not in (None, "Running") or watch.get("paused"):
            try:
                data = parse_remote(offline_dump(), polled)
                r = {**data, "ok": True, "offline": True, "error": None, "polled_at": polled, "fresh_at": None,
                     "offline_at": mtime(WORK / "jl_logs"), "instance": acct.get("instance_status") or "Paused"}
            except Exception as e:
                r.update(ok=False, error=str(e)[:300], polled_at=polled)
            publish(remote=r)
            time.sleep(REMOTE_EVERY)
            continue
        try:
            data = parse_remote(jl("exec", MACHINE, "--", "sh", "-c", REMOTE_SH), polled)
            r = {**data, "ok": True, "error": None, "polled_at": polled, "fresh_at": polled,
                 "latency": round(time.time() - polled, 1)}
        except Exception as e:
            r.update(ok=False, error=str(e)[:300], polled_at=polled)
        publish(remote=r)
        time.sleep(max(0.0, REMOTE_EVERY - (time.time() - polled)))


def account_loop() -> None:
    while True:
        a = dict(STATE.get("account") or {})
        try:
            g = json.loads(jl("get", MACHINE, "--json"))
            a.update(instance_status=g.get("status"), cost=g.get("cost"), runtime=g.get("runtime"),
                     gpu_type=g.get("gpu_type"), name=g.get("name"))
            s = json.loads(jl("status", "--json"))
            a.update(balance=(s.get("balance") or {}).get("balance"), currency=s.get("currency", "INR"),
                     rate=RATE_INR_H, polled_at=time.time(), error=None)
        except Exception as e:
            a.update(error=str(e)[:200], polled_at=time.time())
        publish(account=a)
        time.sleep(60)


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(PAGE.encode(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            with COND:
                return self._send(json.dumps(STATE).encode(), "application/json")
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seen = -1
            try:
                while True:
                    with COND:
                        if VERSION == seen:
                            COND.wait(timeout=15)
                        payload = json.dumps(STATE) if VERSION != seen else None
                        seen = VERSION
                    self.wfile.write(f"data: {payload}\n\n".encode() if payload else b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
        self.send_error(404)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Training Progress</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--ink:#1a1e24;--muted:#5d6876;--line:#e2e6eb;--track:#edf0f4;--accent:#2f6fde;
--ok:#1d8549;--warn:#b26f12;--bad:#c23b3b;--chart2:#d9822b;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0e1115;--card:#161a20;--ink:#e5e8ed;--muted:#929eae;
--line:#252b34;--track:#20262f;--accent:#6c9ffb;--ok:#43c07a;--warn:#e4a84c;--bad:#f06b6b;--chart2:#f0a15a}}
:root[data-theme="dark"]{--bg:#0e1115;--card:#161a20;--ink:#e5e8ed;--muted:#929eae;--line:#252b34;--track:#20262f;
--accent:#6c9ffb;--ok:#43c07a;--warn:#e4a84c;--bad:#f06b6b;--chart2:#f0a15a}
*{box-sizing:border-box}html,body{margin:0}body{background:var(--bg);color:var(--ink);
font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;padding:20px 16px 40px}
.wrap{max-width:1180px;margin:0 auto}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 16px;margin-bottom:14px}
h1{font-size:20px;margin:0;letter-spacing:-.01em}.sub{color:var(--muted);font-size:12.5px}
.live{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;color:var(--muted)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--bad)}.dot.on{background:var(--ok);animation:pulse 2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--ok) 60%,transparent)}70%{box-shadow:0 0 0 7px transparent}100%{box-shadow:0 0 0 0 transparent}}
.chips{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-bottom:16px}
.chip{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.chip b{display:block;font-size:13px}.chip span.t{color:var(--muted);font-size:12.5px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;min-width:0}
.wide{grid-column:1/-1}
.card h2{font-size:15px;margin:0;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.card .where{color:var(--muted);font-size:12px;margin:2px 0 12px}
.badge{font-size:11px;font-weight:600;padding:2px 8px;border-radius:99px;text-transform:uppercase;letter-spacing:.03em;
background:var(--track);color:var(--muted)}
.badge.running{background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--accent)}
.badge.done{background:color-mix(in srgb,var(--ok) 16%,transparent);color:var(--ok)}
.badge.failed{background:color-mix(in srgb,var(--bad) 16%,transparent);color:var(--bad)}
.badge.stopped{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}
.bar{height:10px;background:var(--track);border-radius:99px;overflow:hidden;margin:6px 0 4px}
.bar>i{display:block;height:100%;background:var(--accent);border-radius:99px;transition:width .6s}
.bar.ok>i{background:var(--ok)}
.bar.ind>i{width:100%!important;background:repeating-linear-gradient(-45deg,var(--accent) 0 10px,color-mix(in srgb,var(--accent) 45%,transparent) 10px 20px);
background-size:28px 28px;animation:slide 1s linear infinite;opacity:.55}
@keyframes slide{to{background-position:28px 0}}
.row{display:flex;justify-content:space-between;gap:12px;font-size:12.5px;color:var(--muted);flex-wrap:wrap}
.row b{color:var(--ink);font-weight:600}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:12px 0}
.kv div{background:var(--track);border-radius:8px;padding:8px 10px}.kv small{display:block;color:var(--muted);font-size:11.5px}
.kv strong{font-size:15px;font-variant-numeric:tabular-nums}
.stages{display:grid;grid-template-columns:repeat(6,1fr);gap:6px;margin:4px 0 14px}
.stage{border:1px solid var(--line);border-radius:8px;padding:8px;font-size:12px;min-width:0}
.stage .n{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stage.done{border-color:color-mix(in srgb,var(--ok) 50%,var(--line))}.stage.running{border-color:var(--accent);
box-shadow:0 0 0 1px var(--accent) inset}.stage.failed{border-color:var(--bad)}
.stage .s{color:var(--muted)}.stage.done .s{color:var(--ok)}.stage.running .s{color:var(--accent)}.stage.failed .s{color:var(--bad)}
pre{font:11.5px/1.45 var(--mono);background:var(--track);border-radius:8px;padding:8px 10px;margin:10px 0 0;
max-height:150px;overflow:auto;white-space:pre-wrap;word-break:break-word;color:var(--muted)}
pre.err{color:var(--bad)}
table{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums;margin-top:8px}
th,td{text-align:right;padding:4px 6px;border-bottom:1px solid var(--line)}th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-weight:500}
svg.chart{width:100%;height:120px;display:block;margin-top:8px}
.legend{display:flex;gap:14px;font-size:11.5px;color:var(--muted);margin-top:4px}
.legend i{display:inline-block;width:10px;height:3px;border-radius:2px;margin-right:5px;vertical-align:middle}
.note{font-size:12px;color:var(--muted);margin-top:8px}
.cols{display:grid;grid-template-columns:1.3fr 1fr;gap:18px}
@media (max-width:760px){.stages{grid-template-columns:repeat(3,1fr)}.cols{grid-template-columns:1fr}}
</style></head><body><div class="wrap">
<header><h1>Training progress</h1><span class="live"><span id="dot" class="dot"></span><span id="conn">connecting…</span></span>
<span class="sub" id="fresh"></span></header>
<div class="chips" id="chips"></div>
<div class="grid">
<section class="card wide" id="a100"></section>
<section class="card" id="encoder"></section>
<section class="card" id="laya"></section>
<section class="card" id="swinir"></section>
<section class="card" id="guard"></section>
</div></div>
<script>
const $=s=>document.querySelector(s);let S=null,connected=false;const cache={};
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const LABEL={running:"running",done:"done",failed:"failed",stopped:"stopped",queued:"not started",pending:"waiting"};
function dur(s){if(s==null||!isFinite(s))return"—";s=Math.max(0,Math.round(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;
return h?`${h}h ${String(m).padStart(2,"0")}m`:m?`${m}m ${String(x).padStart(2,"0")}s`:`${x}s`}
const clock=t=>t?new Date(t*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}):"—";
const pct=(a,b)=>b?Math.min(100,100*a/b):0;const n=x=>x==null?"—":Number(x).toLocaleString();
const badge=s=>`<span class="badge ${s}">${LABEL[s]||s}</span>`;
const ago=t=>t?`<span data-ago="${t}"></span>`:"—", left=t=>t?`<span data-left="${t}"></span>`:"—",
  since=t=>t?`<span data-since="${t}"></span>`:"—";
function bar(frac,cls=""){return `<div class="bar ${cls}"><i style="width:${frac.toFixed(2)}%"></i></div>`}
function chart(series,{fmt=v=>v.toFixed(3),xlab=""}={}){
  const pts=series.flatMap(s=>s.pts);if(pts.length<1)return`<div class="note">No points yet.</div>`;
  let x0=Math.min(...pts.map(p=>p[0])),x1=Math.max(...pts.map(p=>p[0])),y0=Math.min(...pts.map(p=>p[1])),y1=Math.max(...pts.map(p=>p[1]));
  if(x1===x0)x1=x0+1;if(y1===y0){y1+=Math.abs(y1)*.05||1;y0-=Math.abs(y0)*.05||1}
  const W=600,H=120,L=44,R=8,T=8,B=18,X=x=>L+(x-x0)/(x1-x0)*(W-L-R),Y=y=>T+(1-(y-y0)/(y1-y0))*(H-T-B);
  let g=`<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><g font-size="10" fill="var(--muted)">
  <text x="${L-6}" y="${T+8}" text-anchor="end">${fmt(y1)}</text><text x="${L-6}" y="${H-B}" text-anchor="end">${fmt(y0)}</text>
  <text x="${L}" y="${H-4}">${x0}</text><text x="${W-R}" y="${H-4}" text-anchor="end">${x1} ${xlab}</text></g>
  <line x1="${L}" x2="${W-R}" y1="${H-B}" y2="${H-B}" stroke="var(--line)"/>`;
  for(const s of series){if(!s.pts.length)continue;const d=s.pts.map(p=>`${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
    g+=`<polyline fill="none" stroke="${s.color}" stroke-width="2" vector-effect="non-scaling-stroke" points="${d}"/>`;
    const l=s.pts[s.pts.length-1];g+=`<circle cx="${X(l[0])}" cy="${Y(l[1])}" r="3" fill="${s.color}"/>`}
  return g+`</svg><div class="legend">${series.map(s=>`<span><i style="background:${s.color}"></i>${s.name}${s.pts.length?` · ${fmt(s.pts[s.pts.length-1][1])}`:""}</span>`).join("")}</div>`}
function logbox(lines,err){return (err?`<pre class="err">${esc(err)}</pre>`:"")+(lines&&lines.length?`<pre>${esc(lines.join("\n"))}</pre>`:"")}
function proc(j){return j.pid?`pid ${j.pid} · CPU ${j.cpu?.toFixed(0)}% · up ${since(j.started)}`:"no process"}

function a100(){
  const r=S.remote||{},a=S.account||{},w=S.watch||{};const st=r.stages||[];
  const cur=st.find(s=>s.state==="running"||s.state==="failed")||[...st].reverse().find(s=>s.state==="done");
  let h=`<h2>JarvisLabs A100-80GB · instance ${esc(a.name||"")} ${badge(r.status==="ALL_DONE"?"done":r.status==="FAILED"?"failed":r.status==="RUNNING"?"running":"pending")}</h2>
  <div class="where">Autopilot status <b>${esc(r.status||"—")}</b> · ${r.offline?`instance <b>${esc(r.instance)}</b> (no GPU billing) · showing the logs the watcher downloaded at ${clock(r.offline_at)}`:`polled ${ago(r.polled_at)}`}${r.latency&&!r.offline?` (${r.latency}s round trip)`:""}${r.ok===false?` · <span style="color:var(--bad)">${esc(r.error)}</span>`:""}</div>`;
  h+=`<div class="stages">${st.map(s=>`<div class="stage ${s.state}"><div class="n">${esc(s.label)}</div><div class="s">${LABEL[s.state]||s.state}</div></div>`).join("")}</div>`;
  h+=`<div class="cols"><div>`;
  if(cur){const p=cur.progress;h+=`<div class="row"><b>${esc(cur.label)}</b><span>${cur.log_at?`last log line ${ago(cur.log_at)}`:""}</span></div>`;
    if(p&&p.total){const f=pct(p.done,p.total);h+=bar(f,cur.state==="done"?"ok":"")+`<div class="row"><span><b>${n(p.done)}</b> / ${n(p.total)} ${p.unit} (${f.toFixed(1)}%)</span>
      <span>${p.rate?esc(p.rate)+" · ":""}${cur.eta_at||p.eta_at?`ETA ${left(cur.eta_at||p.eta_at)}`:""}${cur.evaluating?" · evaluating on val":""}</span></div>`}
    else if(cur.state==="running"){h+=bar(100,"ind")+`<div class="row"><span>running — this stage reports progress only at its end</span></div>`}
    if(cur.summary)h+=`<div class="note">${esc(cur.summary)}</div>`;
    h+=logbox(cur.error?[]:cur.tail,cur.error)}
  const rd=st.find(s=>s.key==="reader");
  if(rd&&rd.curve&&rd.curve.length){const tr=rd.curve.filter(c=>c.loss!=null).map(c=>[c.step,c.loss]),ev=rd.curve.filter(c=>c.eval_loss!=null).map(c=>[c.step,c.eval_loss]);
    h+=`<div class="row" style="margin-top:12px"><b>Reader LoRA loss</b><span>early stopping on val loss, patience 3</span></div>`+chart([{name:"train loss",pts:tr,color:"var(--accent)"},{name:"val loss",pts:ev,color:"var(--chart2)"}],{xlab:"step"})}
  else if(rd&&rd.state==="pending"){h+=`<div class="note">Reader LoRA training starts after reader data is built; its loss curve will appear here.</div>`}
  h+=`</div><div>`;
  const g=r.gpu;h+=`<div class="kv"><div><small>GPU util</small><strong>${g?g.util.toFixed(0)+"%":"—"}</strong></div>
   <div><small>GPU memory</small><strong>${g?(g.mem_used/1024).toFixed(1)+" / "+(g.mem_total/1024).toFixed(0)+" GB":"—"}</strong></div>
   <div><small>Temp · power</small><strong>${g?g.temp.toFixed(0)+"°C · "+g.power.toFixed(0)+" W":"—"}</strong></div></div>`;
  if(g)h+=bar(g.util)+`<div class="row"><span>utilization</span><span>${g.util.toFixed(0)}%</span></div>`;
  const cost=a.cost,bal=a.balance,rate=a.rate||141;
  h+=`<div class="kv"><div><small>Instance</small><strong>${esc(a.instance_status||"—")}</strong></div>
   <div><small>Account balance</small><strong>${bal!=null?"₹"+bal.toFixed(2):"—"}</strong></div>
   <div><small>Rate · runtime</small><strong>₹${rate}/h · ${esc(a.runtime||"—")}</strong></div></div>`;
  h+=`<div class="note">Budget guard: ${w.paused?"instance <b>paused</b> by the watcher":w.alive?`watcher alive; pauses on ALL_DONE / FAILED or at ${clock(w.deadline)} (in ${left(w.deadline)}), at most <b data-spend="${w.deadline}" data-rate="${rate}"></b> more`:`<span style="color:var(--bad)">watcher not running — pause manually with <code>jl pause ${esc(w.machine)} --yes --json</code></span>`}. Account data ${ago(a.polled_at)}.</div>`;
  return h+`</div></div>`}

function encoderCard(){const e=S.encoder;if(!e)return"";const done=e.epochs.filter(x=>x.epoch>=1),k=done.length,M=e.max_epochs;
  let h=`<h2>Semantic encoder · bge-small ${badge(e.state)}</h2><div class="where">Mac · ${proc(e)} → ${esc(e.output)}</div>`;
  if(e.head)h+=`<div class="row"><span>${n(e.head.train)} train · ${n(e.head.val)} val (${n(e.head.val_unseen)} unseen wording)</span><span>${n(e.steps_per_epoch)} steps/epoch</span></div>`;
  const last=e.epochs[e.epochs.length-1];
  if(e.state==="running"){h+=bar(pct(k,M))+`<div class="row"><span>epoch <b>${k+1}</b> of ${M} running for ${since(last&&last.at)}</span><span>${k} done · early stop after 2 non-improving</span></div>`;
    const ts=e.epochs.filter(x=>x.at),est=ts.length>=2?(ts[ts.length-1].at-ts[0].at)/(ts[ts.length-1].epoch-ts[0].epoch):null;
    h+=bar(100,"ind")+`<div class="note">${est?`Measured ${dur(est)} per epoch → finishes in about ${left(last.at+est*(M-k))} if all ${M} epochs run.`:"This trainer logs only at epoch end; the per-epoch time and ETA appear after epoch 1."}</div>`}
  else if(e.state==="done")h+=bar(100,"ok")+`<div class="row"><span>best unseen-wording acc <b>${(e.done.best_val_unseen_acc*100).toFixed(1)}%</b></span></div>`;
  if(e.epochs.length){h+=chart([{name:"val acc (unseen wording)",pts:e.epochs.map(x=>[x.epoch,x.val_unseen_acc*100]),color:"var(--accent)"},
     {name:"val acc (all)",pts:e.epochs.map(x=>[x.epoch,x.val_acc*100]),color:"var(--chart2)"}],{fmt:v=>v.toFixed(1)+"%",xlab:"epoch"});
    h+=`<table><tr><th>epoch</th><th>train loss</th><th>val</th><th>unseen</th><th>seen</th></tr>${e.epochs.map(x=>`<tr><td>${x.epoch===0?"0 (before training)":x.epoch}</td><td>${x.train_loss!=null?x.train_loss.toFixed(4):"—"}</td><td>${(x.val_acc*100).toFixed(1)}%</td><td>${(x.val_unseen_acc*100).toFixed(1)}%</td><td>${(x.val_seen_acc*100).toFixed(1)}%</td></tr>`).join("")}</table>`}
  return h+(e.error?logbox([],e.error):"")}

function layaCard(){const l=S.laya;if(!l)return"";
  let h=`<h2>LAYA v2 · 34 classes ${badge(l.state)}</h2><div class="where">Mac (${esc(l.device||"—")}) · ${proc(l)} → ${esc(l.output)}</div>`;
  if(l.train)h+=`<div class="row"><span>${n(l.train)} train · ${n(l.val)} val decisions</span><span>${n(l.batches)} batches/epoch × ${l.max_epochs}</span></div>`;
  if(l.total_steps){const s=l.step||0,f=pct(s,l.total_steps);h+=bar(f,l.state==="done"?"ok":"")+`<div class="row"><span>step <b>${n(s)}</b> / ${n(l.total_steps)} (${f.toFixed(1)}%) · epoch ${Math.min(l.max_epochs,l.epochs.length+1)} of ${l.max_epochs}${l.resumed_at?` · resumed from step ${n(l.resumed_at)} after the crash`:""}</span><span>${l.loss!=null?`loss ${l.loss.toFixed(4)} · `:""}step logged ${ago(l.ckpt_at)}</span></div>`;
    if(l.rate&&l.state==="running"){const rem=(l.total_steps-s)/l.rate;h+=`<div class="note">${(l.rate*60).toFixed(1)} steps/min (${esc(l.rate_kind)}) → about ${left(l.ckpt_at+rem)} left. Steps are logged every 20; last checkpoint saved ${ago(l.saved_at)}.</div>`}}
  if(l.epochs.length){h+=chart([{name:"val acc",pts:l.epochs.map(x=>[x.epoch,x.val_acc*100]),color:"var(--accent)"}],{fmt:v=>v.toFixed(1)+"%",xlab:"epoch"});
    h+=`<table><tr><th>epoch</th><th>train loss</th><th>val loss</th><th>val acc</th></tr>${l.epochs.map(x=>`<tr><td>${x.epoch}${x.best?" ★":""}</td><td>${x.train_loss.toFixed(4)}</td><td>${x.val_loss.toFixed(4)}</td><td>${(x.val_acc*100).toFixed(1)}%</td></tr>`).join("")}</table>`}
  else h+=`<div class="note">Validation accuracy appears after each epoch.</div>`;
  if(l.exported)h+=`<div class="note">Exported ${esc(l.exported)}</div>`;if(l.stopped)h+=`<div class="note">${esc(l.stopped)}</div>`;
  return h+(l.error?logbox([],l.error):"")}

function swinirCard(){const s=S.swinir;if(!s)return"";
  let h=`<h2>SwinIR-light ×2 ${badge(s.state)}</h2><div class="where">Mac · ${proc(s)}${s.log?" · "+esc(s.log):""}</div>`;
  if(s.state==="queued")return h+`<div class="note">Not started. Runs on the Mac after the encoder and LAYA finish (see docs/RETRAIN_STATUS.md); it is enabled in production only if it improves OCR on low-res pages.</div>`;
  const ep=s.epochs.filter(x=>x.epoch>=1),lv=s.live&&s.state==="running"&&s.live.epoch>ep.length?s.live:null;
  const frac=s.state==="done"?100:pct(ep.length+(lv?lv.step/lv.steps:0),s.max_epochs);
  h+=bar(frac,s.state==="done"?"ok":"")+`<div class="row"><span>${ep.length} of ${s.max_epochs} epochs done${lv?` · epoch <b>${lv.epoch}</b> step <b>${n(lv.step)}</b> / ${n(lv.steps)} · L1 ${lv.l1.toFixed(4)}`:""}</span><span>log ${ago(s.log_at)}</span></div>`;
  if(s.done)h+=`<div class="note">Best val PSNR ${s.done.best_val_psnr.toFixed(2)} dB vs bicubic ${s.done.bicubic.toFixed(2)} dB. Whether it is switched on depends on the OCR comparison, not on PSNR.</div>`;
  if(s.epochs.length)h+=chart([{name:"val PSNR, SwinIR",pts:s.epochs.map(x=>[x.epoch,x.val_psnr_sr]).filter(p=>p[1]!=null),color:"var(--accent)"},
    {name:"val PSNR, bicubic",pts:s.epochs.map(x=>[x.epoch,x.val_psnr_bicubic]).filter(p=>p[1]!=null),color:"var(--chart2)"}],{fmt:v=>v.toFixed(2)+" dB",xlab:"epoch"});
  return h+logbox(s.tail,s.error)}

function guardCard(){const w=S.watch||{},a=S.account||{};
  let h=`<h2>A100 pause guard ${badge(w.paused?"done":w.alive?"running":"failed")}</h2><div class="where">Mac · work/jl_watch2.sh · instance ${esc(w.machine)}</div>`;
  h+=w.paused?`<div class="note">Autopilot ended with <b>${esc(w.final||"?")}</b>; the watcher downloaded the logs to work/jl_logs and paused the instance — GPU billing stopped.${w.final==="FAILED"?" See the failed stage in the A100 card.":""}</div>`:
   w.alive?bar(pct(Date.now()/1000-w.started,w.deadline-w.started))+`<div class="row"><span>hard stop at <b>${clock(w.deadline)}</b></span><span>${left(w.deadline)} left</span></div><div class="note">Checks the autopilot every minute; on ALL_DONE / FAILED / deadline it downloads the adapter and logs, then pauses the instance. Needs this Mac awake.</div>`:
   `<div class="note" style="color:var(--bad)">Watcher not running. GPU billing continues until you run <code>jl pause ${esc(w.machine)} --yes --json</code>.</div>`;
  return h+logbox(w.tail)}

function chips(){const r=S.remote||{},st=r.stages||[],cur=st.find(s=>s.state==="running"||s.state==="failed");
  const c=[];const ap=cur?`${cur.label}${cur.progress&&cur.progress.total?` ${n(cur.progress.done)}/${n(cur.progress.total)}`:""}`:r.status||"—";
  c.push(["A100 autopilot",r.status==="ALL_DONE"?"done":r.status==="FAILED"?"failed":r.status==="RUNNING"?"running":"pending",ap]);
  const e=S.encoder;if(e){const k=e.epochs.filter(x=>x.epoch>=1).length;c.push(["Semantic encoder",e.state,e.state==="done"?"finished":`epoch ${Math.min(k+1,e.max_epochs)}/${e.max_epochs}`])}
  const l=S.laya;if(l)c.push(["LAYA v2",l.state,l.total_steps?`step ${n(l.step||0)}/${n(l.total_steps)}`:"loading"]);
  const s=S.swinir;if(s)c.push(["SwinIR",s.state,s.state==="queued"?"after encoder + LAYA":s.state==="done"?"finished":`epoch ${Math.min(s.max_epochs,s.epochs.filter(x=>x.epoch>=1).length+1)}/${s.max_epochs}`]);
  return c.map(([t,st,txt])=>`<div class="chip"><b>${t} ${badge(st)}</b><span class="t">${esc(txt)}</span></div>`).join("")}

function set(id,html){if(cache[id]===html)return;cache[id]=html;const el=$(id);el.innerHTML=html;el.querySelectorAll("pre").forEach(p=>p.scrollTop=p.scrollHeight)}
function render(){if(!S)return;set("#chips",chips());set("#a100",a100());set("#encoder",encoderCard());set("#laya",layaCard());set("#swinir",swinirCard());set("#guard",guardCard());tick()}
function tick(){const now=Date.now()/1000;
  document.querySelectorAll("[data-ago]").forEach(e=>e.textContent=dur(now-+e.dataset.ago)+" ago");
  document.querySelectorAll("[data-since]").forEach(e=>e.textContent=dur(now-+e.dataset.since));
  document.querySelectorAll("[data-left]").forEach(e=>{const d=+e.dataset.left-now;e.textContent=d>0?dur(d):"any moment"});
  document.querySelectorAll("[data-spend]").forEach(e=>{const h=Math.max(0,+e.dataset.spend-now)/3600;e.textContent="₹"+(h*+e.dataset.rate).toFixed(0)});
  if(S){const r=S.remote||{};$("#fresh").innerHTML=`Mac logs read ${dur(now-S.local_at)} ago · A100 ${r.offline?"paused":r.fresh_at?"polled "+dur(now-r.fresh_at)+" ago":"—"}`}}
function connect(){const es=new EventSource("/events");
  es.onopen=()=>{connected=true;$("#dot").className="dot on";$("#conn").textContent="live"};
  es.onmessage=ev=>{S=JSON.parse(ev.data);render()};
  es.onerror=()=>{connected=false;$("#dot").className="dot";$("#conn").textContent="reconnecting…"}}
connect();setInterval(tick,1000);
</script></body></html>"""


def main() -> None:
    for fn in (local_loop, account_loop, remote_loop):
        threading.Thread(target=fn, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    print(f"training dashboard on http://127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
