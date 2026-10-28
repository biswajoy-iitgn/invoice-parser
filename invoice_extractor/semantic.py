"""Semantic engine: link a printed label or charge description to the canonical field it means.

Different invoices say the same thing differently ("Inv. Dt." == "Invoice Date",
"OTHER CHARGES / THC" == "Terminal Handling Charges", "Hamali" == handling). The engine
fuses independent signals and always returns an explanation:

  1. alias bank   exact match on the normalized / abbreviation-expanded text against curated
                  ontology aliases + reviewer-confirmed aliases (configs/learned_aliases.json)
  2. embedding    fine-tuned bi-encoder (bge-small); cosine similarity to class descriptions and
                  to every alias in the bank, so new reviewer aliases work without retraining
  3. LAYA         fine-tuned cross-encoder choice model over the 34 classes, with context
  4. lexicon      high-precision regex synonyms from schema.CHARGE_CATEGORIES
  5. reader       the reader LLM's own category, when the caller supplies it

Context (transport mode, destination region, incoterm, value shape) is part of the model
input, because "Freight" is Sea or Air Freight and "Security Manifest" is ENS (EU) or AMS (US).
Classes: 9 header targets, 2 auxiliary fallbacks (Master B/L, Chargeable Weight),
22 charge categories, and NONE (a real label/row that is not a target: Seal No, Net Weight,
Party Invoice No, Due Date, Sub Total, B/F ...).

Every model layer is optional: with no fine-tuned weights present the engine degrades to the
alias bank + lexicon + reader vote and says so in `backends`.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .schema import CHARGE_CATEGORIES, CHARGE_FIELDS

ROOT = Path(__file__).resolve().parents[1]
HEADER_CLASSES = ["Invoice No", "Invoice Date", "Container Number", "MAWB No", "HAWB No", "Vendor Name",
                  "Bill of Lading / HBL", "WEIGHT", "Ex Rate"]
AUX_CLASSES = ["Master B/L", "Chargeable Weight"]
CLASSES = HEADER_CLASSES + AUX_CLASSES + list(CHARGE_FIELDS) + ["NONE"]
LABEL_CLASSES = HEADER_CLASSES + AUX_CLASSES + ["NONE"]
CHARGE_CLASSES = list(CHARGE_FIELDS) + ["NONE"]
AUX_TARGET = {"Master B/L": "Bill of Lading / HBL", "Chargeable Weight": "WEIGHT"}

CLASS_DESCRIPTIONS = {
    "Invoice No": "the issuer's own invoice / tax invoice / document / bill number",
    "Invoice Date": "the date the invoice was issued",
    "Container Number": "shipping container identifier, 4 letters + 7 digits",
    "MAWB No": "master air waybill number of an air shipment",
    "HAWB No": "house air waybill number of an air shipment",
    "Vendor Name": "the company that issued the invoice (seller / service provider)",
    "Bill of Lading / HBL": "house bill of lading number of a sea shipment",
    "WEIGHT": "gross weight of the shipment",
    "Ex Rate": "foreign currency to INR exchange rate / rate of exchange",
    "Master B/L": "master / ocean / carrier bill of lading number",
    "Chargeable Weight": "chargeable or volumetric weight of an air shipment",
    "NONE": "a printed label or row that is not one of the target fields (due date, seal number, net weight, "
            "party or shipper invoice number, job number, IRN, acknowledgement, totals, taxes, brought forward)",
}
for _name, _spec in CHARGE_CATEGORIES.items():
    CLASS_DESCRIPTIONS[_name] = _spec["meaning"]

ABBREV = {
    "no": "number", "nos": "numbers", "#": "number", "num": "number", "inv": "invoice", "dt": "date", "dtd": "dated",
    "wt": "weight", "gr": "gross", "chg": "charge", "chgs": "charges", "chrg": "charge", "chrgs": "charges",
    "frt": "freight", "b/l": "bill of lading", "bl": "bill of lading", "hbl": "house bill of lading",
    "h b/l": "house bill of lading", "mbl": "master bill of lading", "awb": "air waybill",
    "hawb": "house air waybill", "mawb": "master air waybill", "cntr": "container", "ctnr": "container",
    "cont": "container", "thc": "terminal handling charges", "o/f": "ocean freight", "a/f": "air freight",
    "doc": "document", "docs": "documents", "exch": "exchange", "roe": "rate of exchange", "fsc": "fuel surcharge",
    "baf": "bunker adjustment factor", "vgm": "verified gross mass", "cfs": "container freight station",
    "s/b": "shipping bill", "cha": "customs house agent", "ihc": "inland haulage charges", "ref": "reference",
    "amt": "amount", "qty": "quantity", "pkgs": "packages", "misc": "miscellaneous", "transp": "transportation",
}
_PUNCT = re.compile(r"[^\w/#&%]+")


def normalize(text: str) -> str:
    """Light normalization used for alias identity: case, punctuation, spacing."""
    t = (text or "").casefold().replace(" ", " ")
    t = re.sub(r"[:\-–—_]+\s*$", "", t.strip())
    t = _PUNCT.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def expand(text: str) -> str:
    """Abbreviation-expanded form ("Inv. Dt." -> "invoice date")."""
    toks = normalize(text).split()
    out, i = [], 0
    while i < len(toks):
        two = " ".join(toks[i:i + 2])
        if two in ABBREV:
            out.append(ABBREV[two])
            i += 2
            continue
        out.append(ABBREV.get(toks[i], toks[i]))
        i += 1
    return " ".join(out)


def region_of(country: str) -> str:
    c = (country or "").casefold()
    if not c:
        return ""
    eu = ("germany", "netherlands", "belgium", "france", "italy", "spain", "poland", "hungary", "austria", "czech",
          "portugal", "sweden", "denmark", "finland", "ireland", "greece", "romania", "slovakia", "slovenia",
          "croatia", "bulgaria", "lithuania", "latvia", "estonia", "luxembourg")
    if any(x in c for x in eu):
        return "EU"
    if "united states" in c or c in ("usa", "us", "u.s.a."):
        return "US"
    if "canada" in c:
        return "CA"
    if "united kingdom" in c or c in ("uk", "u.k."):
        return "UK"
    return "OTHER"


def context_string(kind: str, mode: str = "", region: str = "", incoterm: str = "", value: str = "") -> str:
    parts = [f"mode: {mode or 'unknown'}", f"destination: {region or 'unknown'}"]
    if incoterm:
        parts.append(f"incoterm: {incoterm.split()[0]}")
    if value:
        parts.append(f"value: {value}")
    return " | ".join(parts)


def model_input(kind: str, text: str, context: str) -> str:
    return f"{kind}: {text} | {context}"


@dataclass
class Decision:
    field: str
    confidence: float
    basis: str
    votes: dict = field(default_factory=dict)
    matched_alias: str = ""
    target: str = ""          # output field this decision fills (aux classes map to a fallback target)
    review: str = ""          # set when a business rule asks for a person to check this line

    def explain(self) -> dict:
        out = {"field": self.field, "target": self.target, "confidence": round(self.confidence, 4),
               "basis": self.basis, "matched_alias": self.matched_alias,
               "votes": {k: [v[0], round(v[1], 4)] for k, v in self.votes.items()}}
        if self.review:
            out["review"] = self.review
        return out


class AliasBank:
    """Curated ontology aliases + reviewer-confirmed aliases, keyed by normalized/expanded text."""

    def __init__(self, learned_path: Path | None = None, include_ontology: bool = True):
        self.learned_path = learned_path or Path(os.environ.get(
            "SEMANTIC_LEARNED_ALIASES", ROOT / "configs" / "learned_aliases.json"))
        self.entries: dict[tuple[str, str], tuple[str, str]] = {}   # (kind, key) -> (field, source)
        self._lock = threading.Lock()
        if include_ontology:
            onto = json.loads((ROOT / "configs" / "field_ontology.json").read_text())
            for name, spec in onto.items():
                kind = "label" if name in HEADER_CLASSES else "charge"
                for alias in [name, *spec.get("aliases", []), *spec.get("abbreviations", [])]:
                    if normalize(alias) in ("freight", "freight charges", "container"):
                        continue  # context-dependent words never resolve by identity
                    self._put(kind, alias, name, "ontology")
        for e in self._load_learned():
            self._put(e["kind"], e["text"], e["field"], "learned")

    def _load_learned(self) -> list[dict]:
        try:
            return json.loads(self.learned_path.read_text()).get("aliases", [])
        except (OSError, json.JSONDecodeError):
            return []

    def _put(self, kind: str, text: str, fld: str, source: str) -> None:
        for key in {normalize(text), expand(text)}:
            if key:
                self.entries[(kind, key)] = (fld, source)

    def lookup(self, kind: str, text: str) -> tuple[str, str, str] | None:
        for key in (normalize(text), expand(text)):
            hit = self.entries.get((kind, key))
            if hit:
                return hit[0], hit[1], key
        return None

    def items(self, kind: str) -> list[tuple[str, str]]:
        return [(k[1], v[0]) for k, v in self.entries.items() if k[0] == kind]

    def learn(self, kind: str, text: str, fld: str, reviewer: str = "") -> None:
        if fld not in CLASSES:
            raise ValueError(f"unknown field {fld!r}")
        with self._lock:
            data = {"aliases": self._load_learned()}
            data["aliases"] = [e for e in data["aliases"]
                               if not (e["kind"] == kind and normalize(e["text"]) == normalize(text))]
            data["aliases"].append({"kind": kind, "text": text, "field": fld, "reviewer": reviewer})
            self.learned_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.learned_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            tmp.replace(self.learned_path)
            self._put(kind, text, fld, "learned")

    def unlearn(self, kind: str, text: str) -> bool:
        """Remove a reviewer-confirmed alias (undo for learn). True if one was removed."""
        with self._lock:
            before = self._load_learned()
            kept = [e for e in before
                    if not (e["kind"] == kind and normalize(e["text"]) == normalize(text))]
            if len(kept) == len(before):
                return False
            tmp = self.learned_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"aliases": kept}, indent=2, ensure_ascii=False))
            tmp.replace(self.learned_path)
            dropped = {normalize(text), expand(text)} - {""}
            for key in dropped:
                self.entries.pop((kind, key), None)
            for e in kept:  # restore entries the removed alias had shadowed
                for key in {normalize(e["text"]), expand(e["text"])} & dropped:
                    self._put(e["kind"], e["text"], e["field"], "learned")
            return True


class _Encoder:
    """Fine-tuned bi-encoder (sentence-transformers format)."""

    def __init__(self, path: str):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(path, device="cpu")
        self.cls_emb = self.model.encode([f"class: {c} | {CLASS_DESCRIPTIONS[c]}" for c in CLASSES],
                                         normalize_embeddings=True, convert_to_numpy=True)
        self._alias_cache: dict[str, tuple] = {}

    def scores(self, inputs: list[str], bank: list[tuple[str, str]], kind: str):
        import numpy as np

        q = self.model.encode(inputs, normalize_embeddings=True, convert_to_numpy=True, batch_size=64)
        s = q @ self.cls_emb.T                                    # (n, C)
        if bank:
            key = kind + "|" + str(len(bank))
            if key not in self._alias_cache:
                texts = [model_input(kind, t, "") for t, _ in bank]
                self._alias_cache[key] = (self.model.encode(texts, normalize_embeddings=True,
                                                            convert_to_numpy=True, batch_size=128),
                                          np.array([CLASSES.index(f) for _, f in bank]))
            emb, idx = self._alias_cache[key]
            sa = q @ emb.T                                         # (n, A)
            for c in range(len(CLASSES)):
                m = idx == c
                if m.any():
                    s[:, c] = np.maximum(s[:, c], sa[:, m].max(axis=1))
        return s


class _Laya:
    """Fine-tuned LAYA choice model, scored in batch from raw logits (no Router round-trip)."""

    def __init__(self, path: str):
        import torch
        from safetensors.torch import load_file
        from transformers import AutoTokenizer

        d = Path(path)
        cfg = json.loads((d / "rl_agent_config.json").read_text())
        self.max_len, self.head_max_len = cfg.get("max_len", 512), cfg.get("head_max_len", 192)
        self.tok = AutoTokenizer.from_pretrained(d / "tokenizer")
        from laya.common import build_model as _bm

        self.model = _bm(cfg, encoder_dir=d / "encoder")
        self.model.load_state_dict(load_file(str(d / "model.safetensors")), strict=False)
        self.model.float().eval()
        # CPU by default; LAYA_DEVICE=mps|cuda moves the model to a GPU (about 10x faster per document)
        self.device = os.environ.get("LAYA_DEVICE", "cpu")
        if self.device != "cpu":
            self.model.to(self.device)
        crit = json.loads((d / "criteria.json").read_text()) if (d / "criteria.json").exists() else None
        self.classes = list(crit["criteria"]) if crit else CLASSES
        self.criteria = crit["criteria"] if crit else {c: CLASS_DESCRIPTIONS[c] for c in CLASSES}
        self.instructions = crit["instructions"] if crit else "Which canonical invoice field does this text mean?"
        self.torch = torch

    def probs(self, inputs: list[str]):
        import numpy as np
        from laya.common import QTYPES, build_sequence

        torch = self.torch
        out = np.zeros((len(inputs), len(CLASSES)), dtype=np.float32)
        q = {"t": "choice", "ins": self.instructions, "crit": self.criteria}
        for i0 in range(0, len(inputs), 16):
            batch = []
            for s in inputs[i0:i0 + 16]:
                ids, markers = build_sequence(self.tok, s, q, self.max_len, self.head_max_len)
                batch.append((ids, markers))
            L = max(len(b[0]) for b in batch)
            M = max(len(b[1]) for b in batch)
            ids = torch.full((len(batch), L), self.tok.pad_token_id, dtype=torch.long)
            attn = torch.zeros((len(batch), L), dtype=torch.long)
            pos = torch.zeros((len(batch), M), dtype=torch.long)
            mask = torch.zeros((len(batch), M), dtype=torch.bool)
            for j, (a, m) in enumerate(batch):
                ids[j, :len(a)] = torch.tensor(a)
                attn[j, :len(a)] = 1
                pos[j, :len(m)] = torch.tensor(m)
                mask[j, :len(m)] = True
            dev = self.device
            with torch.no_grad():
                logits, _ = self.model(ids.to(dev), attn.to(dev), pos.to(dev), mask.to(dev),
                                       torch.full((len(batch),), QTYPES["choice"], dtype=torch.long, device=dev))
            p = torch.softmax(logits.float(), dim=-1).cpu().numpy()
            for j in range(len(batch)):
                for k, c in enumerate(self.classes):
                    if c in CLASSES and k < p.shape[1]:
                        out[i0 + j, CLASSES.index(c)] = p[j, k]
        return out


# Rows that are never charges: totals, taxes, round-off, carried/brought forward, summaries.
NON_CHARGE_RE = re.compile(
    r"^\s*(sub\s*-?\s*total|net\s+total|grand\s+total|total(\s+(taxable|invoice|amount|due|payable|value|tax|"
    r"before\s+tax|charges?))*|taxable\s+(value|total|amount)|(add\s*:?\s*)?[cs]gst\d*|igst|utgst|cess|"
    r"round[\s-]*off|b\s*/\s*f|c\s*/\s*f|brought\s+forward|carried\s+forward|tds|net\s+payable|"
    r"amount\s+in\s+words|less\s*:?\s*advance(\s+received)?|tax\s+payable.*)\b[\s:().%\d]*$", re.IGNORECASE)


def _lexicon(text: str) -> list[str]:
    return [n for n, spec in CHARGE_CATEGORIES.items()
            if any(re.search(p, text, re.IGNORECASE) for p in spec["patterns"])]


@lru_cache(maxsize=1)
def _rules() -> list[dict]:
    """Editable business rules (configs/category_rules.json); see the file for the format."""
    path = Path(os.environ.get("CATEGORY_RULES", ROOT / "configs" / "category_rules.json"))
    try:
        rules = json.loads(path.read_text()).get("rules", [])
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for r in rules:
        if r.get("field") not in CLASSES:
            continue
        out.append({**r, "_all": [re.compile(x, re.IGNORECASE) for x in r.get("all", [])],
                    "_any": [re.compile(x, re.IGNORECASE) for x in r.get("any", [])],
                    "_none": [re.compile(x, re.IGNORECASE) for x in r.get("none", [])]})
    return out


def business_rule(text: str, region: str = "") -> dict | None:
    for r in _rules():
        if r.get("regions") and region not in r["regions"]:
            continue
        if any(x.search(text) for x in r["_none"]) or not all(x.search(text) for x in r["_all"]):
            continue
        if r["_any"] and sum(1 for x in r["_any"] if x.search(text)) < int(r.get("min_any", 1)):
            continue
        if not r["_all"] and not r["_any"]:
            continue
        return r
    return None


def _reader_first(votes: dict, alias, learned: bool, rule, matched: str, review: str, w: dict) -> "Decision | None":
    """Category of one charge line. Measured on held-out invoices (270 documents, 1,535 lines; docs/ACCURACY_STUDY.md):

      the exact wording is a known alias                 right 241 of 243                 -> decides
      a business rule matches                            right every time after its fix    -> decides
      embedding model agrees with the reader             right 99.6%                       -> accepted
      embedding disagrees, score >= 0.9                  embedding right 20 of 22          -> the embedding's category, review
      embedding disagrees, score 0.75-0.9, LAYA with it  embedding right (few cases)       -> the embedding's category, review
      embedding disagrees, score < 0.7, LAYA with reader reader right 27 of 28             -> the reader's category, accepted
      anything else                                      about a coin flip                 -> the reader's category, review

    The reader sees the whole invoice; the embedding model compares the wording with every known alias; LAYA mostly
    repeats the reader's mistakes on new wording, so its agreement alone proves little. The earlier weighted sum let
    the small models outvote the reader by score and lost more lines than it won. Returns None when there is no
    reader category to start from (the weighted sum is then used).
    """
    def decided(fld, conf, basis, why=""):
        return Decision(fld, conf, basis, votes, matched, AUX_TARGET.get(fld, fld), why or review)

    if "non-charge-rule" in votes:
        return decided("NONE", 0.99, "non-charge-rule")
    if alias and alias[0] in CHARGE_CLASSES:          # the exact wording is known (taught by a reviewer, or curated)
        return decided(alias[0], 0.995 if learned else 0.99, "alias-bank")
    if rule:
        return decided(rule["field"], 0.99, "business-rule")
    rd = votes.get("reader", (None,))[0]
    if rd not in CHARGE_CLASSES:
        return None
    emb, lay = votes.get("embedding"), votes.get("laya")
    if not emb and not lay:
        return decided(rd, float(w.get("conf_reader_only", 0.93)), "reader")
    if not emb:                                        # only LAYA is loaded
        return decided(rd, 0.97, "reader+laya") if lay[0] == rd else decided(
            rd, float(w.get("conf_reader_alone", 0.45)), "reader", f"the reader read it as {rd}, LAYA as {lay[0]}")
    lay_c = lay[0] if lay else None
    if emb[0] == rd:
        return decided(rd, float(w.get("conf_all_agree", 0.99) if lay_c in (rd, None) else w.get("conf_reader_plus_one", 0.97)),
                       "reader+embedding" + ("+laya" if lay_c == rd else ""))
    # The embedding model reads the line differently. Its score decides how much that counts.
    strong, agreed, doubt = (float(w.get(k, v)) for k, v in (("embedding_override_min", 0.9),
                                                              ("model_override_min", 0.75), ("embedding_doubt_min", 0.7)))
    if emb[0] in CHARGE_CLASSES and (emb[1] >= strong or (emb[1] >= agreed and lay_c == emb[0])):
        return decided(emb[0], float(w.get("conf_models_override", 0.8)), "embedding" + ("+laya" if lay_c == emb[0] else ""),
                       f"the reader read it as {rd}; the embedding model is confident it is {emb[0]}")
    if emb[1] < doubt and lay_c == rd:
        return decided(rd, float(w.get("conf_reader_laya", 0.93)), "reader+laya")
    other = " / ".join(dict.fromkeys(x for x in (emb[0], lay_c) if x and x != rd))
    return decided(rd, float(w.get("conf_reader_alone", 0.45)), "reader",
                   f"the reader read it as {rd}, the semantic models as {other}")


def rule_conflict(text: str, region: str = "") -> dict | None:
    """A rule whose own wording is on the line but which an exclusion word switched off: the line reads both ways
    ("EXPORT CHARGES Haulage (delivery)"), so a person should look at it."""
    for r in _rules():
        if r.get("regions") and region not in r["regions"]:
            continue
        if not r["_none"] or not any(x.search(text) for x in r["_none"]):
            continue
        if (r["_all"] or r["_any"]) and all(x.search(text) for x in r["_all"]) and (
                not r["_any"] or sum(1 for x in r["_any"] if x.search(text)) >= int(r.get("min_any", 1))):
            return r
    return None


@lru_cache(maxsize=1)
def _weights() -> dict:
    path = Path(os.environ.get("SEMANTIC_FUSION", ROOT / "configs" / "semantic_fusion.json"))
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"embedding": 1.0, "embedding_temperature": 0.05, "laya": 1.0, "lexicon": 0.35, "reader": 0.6,
                "alias_bonus": 3.0}


class SemanticEngine:
    def __init__(self, encoder_dir: str | None = None, laya_dir: str | None = None,
                 bank: AliasBank | None = None):
        self.bank = bank or AliasBank()
        self.backends = ["alias-bank", "lexicon"]
        self.encoder = self.laya = None
        enc = encoder_dir or os.environ.get("SEMANTIC_ENCODER_DIR", str(ROOT / "checkpoints" / "semantic-encoder"))
        lay = laya_dir or os.environ.get("LAYA_MODEL_DIR", str(ROOT / "checkpoints" / "laya-invoice-v2" / "laya-invoice-ft"))
        if os.environ.get("SEMANTIC_DISABLE_MODELS") != "1":
            if Path(enc).exists():
                try:
                    self.encoder = _Encoder(enc)
                    self.backends.append("embedding")
                except Exception as exc:  # pragma: no cover - optional dependency
                    self.backends.append(f"embedding-unavailable:{type(exc).__name__}")
            if Path(lay).exists():
                try:
                    self.laya = _Laya(lay)
                    self.backends.append("laya")
                except Exception as exc:  # pragma: no cover
                    self.backends.append(f"laya-unavailable:{type(exc).__name__}")
        self._lock = threading.Lock()

    def classify(self, items: list[dict]) -> list[Decision]:
        """items: [{"kind": "label"|"charge", "text": str, "mode", "region", "incoterm", "value", "reader"}]."""
        import numpy as np

        if not items:
            return []
        w = _weights()
        inputs = [model_input(it["kind"], it["text"], context_string(
            it["kind"], it.get("mode", ""), it.get("region", ""), it.get("incoterm", ""), it.get("value", "")))
            for it in items]
        logit = np.zeros((len(items), len(CLASSES)), dtype=np.float64)
        votes: list[dict] = [{} for _ in items]
        with self._lock:
            if self.encoder is not None:
                for kind in ("label", "charge"):
                    idx = [i for i, it in enumerate(items) if it["kind"] == kind]
                    if not idx:
                        continue
                    s = self.encoder.scores([inputs[i] for i in idx], self.bank.items(kind), kind)
                    z = s / w["embedding_temperature"]
                    z = z - z.max(axis=1, keepdims=True)
                    p = np.exp(z) / np.exp(z).sum(axis=1, keepdims=True)
                    for r, i in enumerate(idx):
                        logit[i] += w["embedding"] * np.log(p[r] + 1e-6)
                        k = int(p[r].argmax())
                        votes[i]["embedding"] = (CLASSES[k], float(p[r, k]))
            if self.laya is not None:
                p = self.laya.probs(inputs)
                for i in range(len(items)):
                    logit[i] += w["laya"] * np.log(p[i] + 1e-6)
                    k = int(p[i].argmax())
                    votes[i]["laya"] = (CLASSES[k], float(p[i, k]))
        out = []
        for i, it in enumerate(items):
            allowed = LABEL_CLASSES if it["kind"] == "label" else CHARGE_CLASSES
            mask = np.array([c in allowed for c in CLASSES])
            alias = self.bank.lookup(it["kind"], it["text"])
            matched = ""
            if alias:
                fld, src, key = alias
                votes[i]["alias-bank"] = (fld, 1.0)
                matched = f"{key} ({src})"
                if fld in CLASSES:
                    logit[i, CLASSES.index(fld)] += w["alias_bonus"]
            review = ""
            learned = bool(alias) and "learned" in str(alias[1])
            rule = business_rule(it["text"], it.get("region", "")) if it["kind"] == "charge" and not learned else None
            if rule:
                votes[i]["business-rule"] = (rule["field"], 1.0)
                matched = f"rule:{rule['name']}"
                logit[i, CLASSES.index(rule["field"])] += 2 * w["alias_bonus"] + w.get("reader", 0.6) + 20.0
                review = rule.get("why", rule["name"]) if rule.get("review") else ""
            if it["kind"] == "charge" and NON_CHARGE_RE.match(it["text"]):
                votes[i]["non-charge-rule"] = ("NONE", 1.0)
                logit[i, CLASSES.index("NONE")] += w["alias_bonus"] + w.get("reader", 0.6)
            if it["kind"] == "charge":
                lex = _lexicon(it["text"])
                if lex:
                    votes[i]["lexicon"] = (lex[0] if len(lex) == 1 else "|".join(lex), 1.0 / len(lex))
                    for c in lex:
                        logit[i, CLASSES.index(c)] += w["lexicon"] / len(lex)
            rd = it.get("reader")
            if rd and rd in CLASSES:
                votes[i]["reader"] = (rd, 1.0)
                logit[i, CLASSES.index(rd)] += w["reader"]
            if it["kind"] == "charge" and w.get("charge_policy", "reader-first") == "reader-first":
                d = _reader_first(votes[i], alias, learned, rule, matched, review, w)
                if d is not None:
                    clash = rule_conflict(it["text"], it.get("region", "")) if not rule and not learned else None
                    if clash and clash["field"] != d.field and not d.review:
                        d.review = (f"the wording also fits {clash['field']} (rule '{clash['name']}'), but another "
                                    "word on the line points the other way")
                    out.append(d)
                    continue
            z = np.where(mask, logit[i], -1e9)
            if not votes[i]:
                fld = "Other Charges" if it["kind"] == "charge" else "NONE"
                out.append(Decision(fld, 0.0, "no-signal", {}, "", AUX_TARGET.get(fld, fld)))
                continue
            z = z - z.max()
            p = np.exp(z)
            p /= p.sum()
            k = int(p.argmax())
            fld = CLASSES[k]
            basis = "+".join(sorted(v for v, (f, _) in votes[i].items() if f == fld or fld in f.split("|"))) or "fusion"
            out.append(Decision(fld, float(p[k]), basis, votes[i], matched, AUX_TARGET.get(fld, fld), review))
        return out

    def learn(self, kind: str, text: str, fld: str, reviewer: str = "") -> None:
        self.bank.learn(kind, text, fld, reviewer)

    def revert(self, kind: str, text: str) -> bool:
        return self.bank.unlearn(kind, text)


_ENGINE: SemanticEngine | None = None
_ENGINE_LOCK = threading.Lock()


def engine() -> SemanticEngine:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = SemanticEngine()
        return _ENGINE


def softmax_entropy(p) -> float:
    return -sum(x * math.log(x + 1e-12) for x in p)
