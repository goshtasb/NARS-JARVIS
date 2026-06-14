"""Gated-pipeline harness (v1.24.0 extraction redesign): guarded direct extraction -> deterministic gate,
over the 12-clause legal corpus, on the real 7B. Two phases:

  phase 1 (no labels file): extract + gate every clause, dump `gated_emitted.json` (every proposed claim,
           its gate verdict, and the kept/degraded output) for hand-labeling of ground-truth faithfulness.
  phase 2 (labels present) : load `ground_truth_labels.json` and compute the gate's precision/recall as a
           classifier whose positive action is "allow a FAITHFUL assertion into L2".

  TP = gate admits, kept output faithful   FP = gate admits, kept output UNfaithful (a LEAK)
  TN = gate rejects, proposed unfaithful   FN = gate rejects, proposed faithful (over-block / omission)
  precision = TP/(TP+FP)   recall = TP/(TP+FN)

Run: NARS_JARVIS_LLM_GGUF=<7b>  python tools/extraction_harness/run_gated.py   (from repo root)
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "src")
sys.path.insert(0, _SRC)

from language.guarded_extract import extract_guarded   # noqa: E402
from language.llm import LocalLLM                       # noqa: E402
from language.verify_gate import verify                 # noqa: E402

_EMITTED = os.path.join(_HERE, "gated_emitted.json")
_LABELS = os.path.join(_HERE, "ground_truth_labels.json")


def _claim_key(cid: str, idx: int) -> str:
    return f"{cid}#{idx}"


def extract_and_gate() -> dict:
    corpus = json.load(open(os.path.join(_HERE, "legal_corpus.json")))
    llm = LocalLLM()
    out = {}
    for i, case in enumerate(corpus["cases"]):
        sys.stderr.write(f"[{i+1}/{len(corpus['cases'])}] {case['id']}\n"); sys.stderr.flush()
        proposed = extract_guarded(llm, case["text"])
        rows = []
        for j, claim in enumerate(proposed):
            r = verify(claim, case["text"])
            rows.append({"key": _claim_key(case["id"], j), "proposed": claim,
                         "admit": r.admit, "degraded": r.degraded,
                         "reasons": r.reasons, "kept": r.kept})
        out[case["id"]] = {"text": case["text"], "claims": rows}
    json.dump(out, open(_EMITTED, "w"), indent=2)
    return out


def score(emitted: dict, labels: dict) -> dict:
    tp = fp = tn = fn = 0
    unlabeled = []
    confusion = []
    for cid, case in emitted.items():
        for row in case["claims"]:
            key = row["key"]
            lab = labels.get(key)
            if lab is None:
                unlabeled.append(key)
                continue
            proposed_faithful = bool(lab["proposed_faithful"])
            # the faithfulness of what the gate would actually WRITE:
            kept_faithful = bool(lab.get("kept_faithful", proposed_faithful)) if row["degraded"] else proposed_faithful
            if row["admit"]:
                cell = "TP" if kept_faithful else "FP"
            else:
                cell = "FN" if proposed_faithful else "TN"
            confusion.append({"key": key, "cell": cell, "admit": row["admit"],
                              "degraded": row["degraded"], "reasons": row["reasons"]})
            tp += cell == "TP"; fp += cell == "FP"; tn += cell == "TN"; fn += cell == "FN"
    precision = round(tp / (tp + fp), 3) if (tp + fp) else None
    recall = round(tp / (tp + fn), 3) if (tp + fn) else None
    return {"TP": tp, "FP": fp, "TN": tn, "FN": fn, "precision": precision, "recall": recall,
            "n_unlabeled": len(unlabeled), "unlabeled": unlabeled, "confusion": confusion}


def regate(emitted: dict) -> dict:
    """Re-run the (current) gate over the CACHED proposed claims — isolates a gate change from model
    stochasticity, so a precision/recall delta is attributable purely to the gate logic."""
    for case in emitted.values():
        for row in case["claims"]:
            r = verify(row["proposed"], case["text"])
            row["admit"], row["degraded"], row["reasons"], row["kept"] = r.admit, r.degraded, r.reasons, r.kept
    json.dump(emitted, open(_EMITTED, "w"), indent=2)
    return emitted


def main() -> int:
    if not os.path.exists(_EMITTED) or "--reextract" in sys.argv:
        emitted = extract_and_gate()
        sys.stderr.write(f"wrote {_EMITTED}\n")
    else:
        emitted = json.load(open(_EMITTED))
        if "--regate" in sys.argv:
            emitted = regate(emitted)
            sys.stderr.write("re-gated cached claims with current verify()\n")
    if not os.path.exists(_LABELS):
        n = sum(len(c["claims"]) for c in emitted.values())
        print(f"PHASE 1 complete: {n} claims emitted across {len(emitted)} clauses -> {_EMITTED}")
        print("Create ground_truth_labels.json {<key>: {proposed_faithful, kept_faithful?}} then re-run.")
        return 0
    labels = json.load(open(_LABELS))
    res = score(emitted, labels)
    json.dump(res, open(os.path.join(_HERE, "gated_results.json"), "w"), indent=2)
    print("\n" + "=" * 60)
    print("GATED PIPELINE — gate precision/recall (real 7B)")
    print("=" * 60)
    print(f"  TP={res['TP']}  FP={res['FP']}  TN={res['TN']}  FN={res['FN']}   (unlabeled={res['n_unlabeled']})")
    print(f"  precision = {res['precision']}   recall = {res['recall']}")
    print("  FP (LEAKS — unfaithful admitted):",
          [c["key"] for c in res["confusion"] if c["cell"] == "FP"] or "none")
    print("  FN (faithful over-blocked):",
          [c["key"] for c in res["confusion"] if c["cell"] == "FN"] or "none")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
