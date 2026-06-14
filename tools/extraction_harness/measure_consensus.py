"""Measure the FULL final pipeline (3-pass perturbation consensus -> tripwire gate) precision/recall on the
12-clause corpus, reusing the existing hand labels by matching claims on their content key (so no
re-labeling). A claim DROPPED by consensus is treated as rejected (FN if it was faithful, else TN); a
survivor is run through the gate exactly as before. Run from repo root with NARS_JARVIS_LLM_GGUF set."""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "src"))

from language.consensus import _consensus_key, extract_consensus  # noqa: E402
from language.llm import LocalLLM                                  # noqa: E402
from language.verify_gate import verify                            # noqa: E402


def main() -> int:
    corpus = {c["id"]: c for c in json.load(open(os.path.join(_HERE, "legal_corpus.json")))["cases"]}
    labels = json.load(open(os.path.join(_HERE, "ground_truth_labels.json")))
    emitted = json.load(open(os.path.join(_HERE, "gated_emitted.json")))

    # map content-key -> label, per clause, from the original position-keyed labels + proposed claims
    label_by_ckey = {}
    for cid, case in emitted.items():
        for idx, row in enumerate(case["claims"]):
            lab = labels.get(f"{cid}#{idx}")
            if lab:
                label_by_ckey[(cid, _consensus_key(row["proposed"]))] = lab

    temps = tuple(float(x) for x in sys.argv[1].split(",")) if len(sys.argv) > 1 else (0.0, 0.4, 0.7)
    llm = LocalLLM()
    tp = fp = tn = fn = 0
    dropped_by_consensus = []
    leaks = []
    print(f"(perturbation temps = {temps})")
    for cid, case in corpus.items():
        sys.stderr.write(f"consensus: {cid}\n"); sys.stderr.flush()
        survivors = extract_consensus(llm, case["text"], temps=temps)
        survivor_keys = {_consensus_key(s): s for s in survivors}
        # iterate the ORIGINAL labeled claims for this clause
        for (lcid, ckey), lab in label_by_ckey.items():
            if lcid != cid:
                continue
            pf = bool(lab["proposed_faithful"])
            if ckey not in survivor_keys:                       # consensus dropped it
                if pf:
                    fn += 1; dropped_by_consensus.append((cid, "faithful->FN"))
                else:
                    tn += 1; dropped_by_consensus.append((cid, "unfaithful->TN"))
                continue
            r = verify(survivor_keys[ckey], case["text"])       # survived -> gate it
            if r.admit:
                kf = bool(lab.get("kept_faithful", pf)) if r.degraded else pf
                if kf:
                    tp += 1
                else:
                    fp += 1; leaks.append(cid)
            else:
                fn += 1 if pf else 0
                tn += 1 if not pf else 0

    precision = round(tp / (tp + fp), 3) if (tp + fp) else None
    recall = round(tp / (tp + fn), 3) if (tp + fn) else None
    print("\n" + "=" * 60)
    print("FULL PIPELINE (consensus + tripwire gate) — real 7B")
    print("=" * 60)
    print(f"  TP={tp}  FP={fp}  TN={tn}  FN={fn}")
    print(f"  precision = {precision}   recall = {recall}")
    print(f"  LEAKS (unfaithful admitted): {leaks or 'none'}")
    print(f"  consensus drops: {len(dropped_by_consensus)} -> {dropped_by_consensus}")
    print("=" * 60)
    json.dump({"TP": tp, "FP": fp, "TN": tn, "FN": fn, "precision": precision, "recall": recall,
               "leaks": leaks, "dropped_by_consensus": dropped_by_consensus},
              open(os.path.join(_HERE, "consensus_results.json"), "w"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
