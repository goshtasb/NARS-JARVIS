"""Empirical extraction harness (v1.24.0 extraction-pipeline audit) — Synapse directive.

Measures, on the REAL configured 7B (temp 0, the project's actual code paths), the three questions:

  Path A  (current)  : summarize the clause, THEN extract claims from the summary.
  Path B  (proposed) : extract claims DIRECTLY from the raw clause.
  Path A-DOC         : concatenate ALL clauses into one "document", run the real Map-Reduce
                       summarize, then extract — the realistic 50-page compression analog.
  Path C  (expanded) : extract under an EXPANDED GBNF that adds Conditional + Temporal claim
                       shapes, to measure whether richer structure lets the 7B bind legal
                       conditionals/deadlines — or collapses into degenerate output.

All model output is REAL. Scoring is deterministic and transparent:
  * content_recall  — fraction of a clause's hand-labeled critical constraints whose key_tokens
                      appear (normalized substring) anywhere in the extracted claim atoms. Measures
                      whether the CONTENT survived (NOT whether it is correctly bound).
  * expressibility  — purely analytical, model-independent: a constraint is structurally
                      expressible IFF its kind is in {relational, property} (the 4-shape grammar).
  * fabrication     — heuristic: content tokens (len>3, non-stopword) in an extracted atom that do
                      NOT appear in the source clause. Flagged for human confirmation, not asserted.

Run:  NARS_JARVIS_LLM_GGUF=<7b.gguf>  python -m tools.extraction_harness.run_harness   (cwd=src)
Writes tools/extraction_harness/results.json and prints a summary table.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "src")
sys.path.insert(0, _SRC)

from actions import documents          # noqa: E402  — real Map-Reduce + chunker
from language import claims_to_narsese, parse_claims  # noqa: E402
from language.llm import LocalLLM      # noqa: E402

_EXTRACT_PROMPT = ("Extract the factual claims stated in the text as structured JSON: "
                   "subject-relation-object (RelationClaim) and subject-property (PropertyClaim). "
                   "Assert ONLY what the text states. If nothing factual is asserted, return empty lists.")
_EXPRESSIBLE = {"relational", "property"}
_STOP = frozenset(("shall", "will", "must", "with", "within", "from", "that", "this", "such", "the",
                   "and", "any", "for", "not", "than", "upon", "into", "per", "all", "other", "its",
                   "their", "each", "more", "less", "have", "been", "which", "party", "parties"))

# ── normalization + deterministic scoring ──
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9.%]+", " ", (s or "").lower()).strip()


def _claim_atoms_text(claims) -> str:
    """All field values of typed claims, flattened to one normalized string for token matching."""
    parts = []
    for c in claims:
        for attr in ("subject", "verb", "object", "value"):
            v = getattr(c, attr, None)
            if v:
                parts.append(str(v))
    return _norm(" ".join(parts).replace("_", " "))


def _constraint_captured(constraint: dict, haystack: str) -> bool:
    """True if ANY alternative token-set is fully present (each token a normalized substring)."""
    for alt in constraint["key_tokens"]:
        if all(_norm(tok) and _norm(tok) in haystack for tok in alt):
            return True
    return False


def _content_recall(case: dict, claims) -> dict:
    hay = _claim_atoms_text(claims)
    hits = [c["id"] for c in case["critical_constraints"] if _constraint_captured(c, hay)]
    total = len(case["critical_constraints"])
    return {"captured": hits, "n_captured": len(hits), "n_total": total,
            "recall": round(len(hits) / total, 3) if total else 0.0}


def _fabrication(case: dict, claims) -> list[str]:
    src = _norm(case["text"])
    flagged = []
    for c in claims:
        for attr in ("subject", "verb", "object", "value"):
            v = getattr(c, attr, None)
            if not v:
                continue
            for tok in _norm(str(v).replace("_", " ")).split():
                if len(tok) > 3 and tok not in _STOP and tok not in src and tok not in flagged:
                    flagged.append(tok)
    return flagged


def _expressibility(case: dict) -> dict:
    kinds = [c["kind"] for c in case["critical_constraints"]]
    inexpr = [k for k in kinds if k not in _EXPRESSIBLE]
    return {"n_total": len(kinds), "n_inexpressible": len(inexpr),
            "inexpressible_kinds": sorted(set(inexpr)),
            "structural_omission_rate": round(len(inexpr) / len(kinds), 3) if kinds else 0.0}


# ── expanded GBNF (Path C): adds Conditional + Temporal shapes ──
_EXPANDED_GRAMMAR = r"""
root        ::= ws "[" ws ( claim ( ws "," ws claim )* )? ws "]" ws
claim       ::= relation | property | conditional | temporal
relation    ::= "{" ws "\"type\"" ws ":" ws "\"RelationClaim\"" ws "," ws "\"subject\"" ws ":" ws string ws "," ws "\"verb\"" ws ":" ws string ws "," ws "\"object\"" ws ":" ws string ws "}"
property    ::= "{" ws "\"type\"" ws ":" ws "\"PropertyClaim\"" ws "," ws "\"subject\"" ws ":" ws string ws "," ws "\"value\"" ws ":" ws string ws "}"
conditional ::= "{" ws "\"type\"" ws ":" ws "\"ConditionalClaim\"" ws "," ws "\"if\"" ws ":" ws string ws "," ws "\"then\"" ws ":" ws string ws "}"
temporal    ::= "{" ws "\"type\"" ws ":" ws "\"TemporalClaim\"" ws "," ws "\"subject\"" ws ":" ws string ws "," ws "\"action\"" ws ":" ws string ws "," ws "\"object\"" ws ":" ws string ws "," ws "\"within_value\"" ws ":" ws string ws "," ws "\"within_unit\"" ws ":" ws string ws "}"
string      ::= "\"" char* "\""
char        ::= [a-zA-Z0-9_] | " " | "-" | "."
ws          ::= [ \t\n]*
"""

_EXPANDED_PROMPT = (
    "Extract the legal meaning as structured JSON claims. Use the richest shape that fits:\n"
    "- RelationClaim {subject, verb, object} for a binary fact.\n"
    "- PropertyClaim {subject, value} for an attribute.\n"
    "- ConditionalClaim {if, then} for an if/trigger -> consequence (e.g. a breach triggering a duty).\n"
    "- TemporalClaim {subject, action, object, within_value, within_unit} for a deadline (e.g. notify "
    "within 72 hours -> within_value='72', within_unit='hours').\n"
    "Bind deadlines and conditions to the action they govern. Assert ONLY what the text states.")


def _score_pathC(case: dict, raw: str) -> dict:
    """Did the expanded grammar let the 7B BIND conditionals/deadlines, or collapse? Deterministic."""
    try:
        arr = json.loads(raw)
        valid_json = isinstance(arr, list)
    except ValueError:
        return {"valid_json": False, "n_claims": 0, "conditional": 0, "temporal": 0,
                "degenerate_fields": 0, "deadline_bound": False, "condition_bound": False}
    if not valid_json:
        arr = []
    cond = [c for c in arr if isinstance(c, dict) and c.get("type") == "ConditionalClaim"]
    temp = [c for c in arr if isinstance(c, dict) and c.get("type") == "TemporalClaim"]
    # degenerate = a claim with an empty-string field (grammar-valid but meaningless)
    degen = 0
    for c in arr:
        if isinstance(c, dict) and any(isinstance(v, str) and not v.strip()
                                       for k, v in c.items() if k != "type"):
            degen += 1
    # is the clause's temporal constraint correctly bound? (a TemporalClaim whose value/unit match)
    deadline_bound = False
    for c in temp:
        blob = _norm(" ".join(str(v) for v in c.values()))
        for con in case["critical_constraints"]:
            if con["kind"] == "temporal" and _constraint_captured(con, blob):
                deadline_bound = True
    condition_bound = False
    for c in cond:
        blob = _norm(" ".join(str(v) for v in c.values()))
        for con in case["critical_constraints"]:
            if con["kind"] == "conditional" and _constraint_captured(con, blob):
                condition_bound = True
    return {"valid_json": valid_json, "n_claims": len(arr), "conditional": len(cond),
            "temporal": len(temp), "degenerate_fields": degen,
            "deadline_bound": deadline_bound, "condition_bound": condition_bound}


# ── runners ──
def main() -> int:
    corpus = json.load(open(os.path.join(_HERE, "legal_corpus.json")))
    cases = corpus["cases"]
    llm = LocalLLM()
    gen_text = lambda s, u, mt: llm.generate_text(s, u, max_tokens=mt)  # noqa: E731

    results = []
    t_start = time.time()
    for i, case in enumerate(cases):
        sys.stderr.write(f"[{i+1}/{len(cases)}] {case['id']}\n"); sys.stderr.flush()
        text = case["text"]

        # Path B — direct extraction
        rawB = llm.generate(_EXTRACT_PROMPT, text)
        claimsB = parse_claims(rawB)

        # Path A — summarize (real Map-Reduce) then extract
        summary = documents.summarize(text, gen_text)
        rawA = llm.generate(_EXTRACT_PROMPT, summary)
        claimsA = parse_claims(rawA)

        # Path C — expanded grammar
        rawC = llm.generate_json(_EXPANDED_PROMPT, text, _EXPANDED_GRAMMAR, max_tokens=512)

        results.append({
            "id": case["id"], "category": case["category"], "text": text,
            "expressibility": _expressibility(case),
            "pathB_direct": {"raw": rawB, "narsese": claims_to_narsese(claimsB),
                             "recall": _content_recall(case, claimsB),
                             "fabrication": _fabrication(case, claimsB)},
            "pathA_summary": {"summary": summary, "raw": rawA, "narsese": claims_to_narsese(claimsA),
                              "recall": _content_recall(case, claimsA),
                              "fabrication": _fabrication(case, claimsA)},
            "pathC_expanded": {"raw": rawC, "score": _score_pathC(case, rawC)},
        })

    # Path A-DOC — the realistic full-document compression analog
    sys.stderr.write("[doc] aggregate Map-Reduce over all clauses\n"); sys.stderr.flush()
    doc = "\n\n".join(c["text"] for c in cases)
    doc_summary = documents.summarize(doc, gen_text)
    doc_raw = llm.generate(_EXTRACT_PROMPT, doc_summary)
    doc_claims = parse_claims(doc_raw)
    all_constraints = sum(len(c["critical_constraints"]) for c in cases)
    doc_hay = _claim_atoms_text(doc_claims)
    doc_captured = sum(1 for case in cases for con in case["critical_constraints"]
                       if _constraint_captured(con, doc_hay))

    summary_block = _aggregate(results, doc_summary, doc_raw, claims_to_narsese(doc_claims),
                               doc_captured, all_constraints, time.time() - t_start)
    out = {"meta": corpus["_meta"], "aggregate": summary_block, "cases": results}
    json.dump(out, open(os.path.join(_HERE, "results.json"), "w"), indent=2)
    _print_report(summary_block)
    return 0


def _aggregate(results, doc_summary, doc_raw, doc_narsese, doc_captured, all_constraints, elapsed) -> dict:
    n = len(results)
    avg = lambda key: round(sum(r[key]["recall"]["recall"] for r in results) / n, 3)  # noqa: E731
    return {
        "n_cases": n,
        "elapsed_sec": round(elapsed, 1),
        "avg_recall_pathB_direct": avg("pathB_direct"),
        "avg_recall_pathA_summary": avg("pathA_summary"),
        "avg_structural_omission_rate": round(
            sum(r["expressibility"]["structural_omission_rate"] for r in results) / n, 3),
        "total_constraints": sum(r["expressibility"]["n_total"] for r in results),
        "total_inexpressible": sum(r["expressibility"]["n_inexpressible"] for r in results),
        "pathC_valid_json_rate": round(sum(1 for r in results if r["pathC_expanded"]["score"]["valid_json"]) / n, 3),
        "pathC_deadline_bound": sum(1 for r in results if r["pathC_expanded"]["score"]["deadline_bound"]),
        "pathC_condition_bound": sum(1 for r in results if r["pathC_expanded"]["score"]["condition_bound"]),
        "pathC_total_degenerate_fields": sum(r["pathC_expanded"]["score"]["degenerate_fields"] for r in results),
        "docA": {"summary_chars": len(doc_summary), "narsese": doc_narsese,
                 "captured": doc_captured, "total_constraints": all_constraints,
                 "recall": round(doc_captured / all_constraints, 3) if all_constraints else 0.0},
    }


def _print_report(a: dict) -> None:
    print("\n" + "=" * 72)
    print("EXTRACTION HARNESS — RESULTS (real 7B, temp 0)")
    print("=" * 72)
    print(f"cases: {a['n_cases']}   elapsed: {a['elapsed_sec']}s")
    print(f"\nCONTENT RECALL (did the constraint's content survive, bound or not):")
    print(f"  Path B (direct extract)   : {a['avg_recall_pathB_direct']}")
    print(f"  Path A (summarize->extract): {a['avg_recall_pathA_summary']}")
    print(f"  Path A-DOC (12 clauses->1 summary->extract): {a['docA']['recall']}  "
          f"({a['docA']['captured']}/{a['docA']['total_constraints']}; summary={a['docA']['summary_chars']} chars)")
    print(f"\nSTRUCTURAL EXPRESSIBILITY (analytical, model-independent):")
    print(f"  inexpressible in 4-shape grammar: {a['total_inexpressible']}/{a['total_constraints']}"
          f"  (omission rate {a['avg_structural_omission_rate']})")
    print(f"\nPATH C — EXPANDED GRAMMAR (conditional + temporal shapes):")
    print(f"  valid JSON rate           : {a['pathC_valid_json_rate']}")
    print(f"  deadlines correctly bound : {a['pathC_deadline_bound']} clauses")
    print(f"  conditions correctly bound: {a['pathC_condition_bound']} clauses")
    print(f"  degenerate (empty) fields : {a['pathC_total_degenerate_fields']}")
    print("=" * 72)
    print("Full raw outputs in tools/extraction_harness/results.json")


if __name__ == "__main__":
    raise SystemExit(main())
