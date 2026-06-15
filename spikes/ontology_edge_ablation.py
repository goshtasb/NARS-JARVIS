"""Ontology edge-proposal — ABLATION (Phase-1 controlled re-run).

Tests whether the first run's yield collapse (2 edges / 16 clauses, one a degenerate self-loop) was a
HARNESS artifact (correlated 3x-temperature consensus) or a real MODEL failure. Precision is NOT traded
away: the original recall-killing temperature axis is replaced by a DECORRELATED, precision-biased gate:

    admit edge e  <=>  grounded_verbatim(e)  AND  source != target  AND  invariant_across(SetA, SetB)

Anti-mimicry (Challenge 1): two few-shot sets with DISJOINT node vocabularies teach the same FORM; an edge
must survive BOTH primings. A mimicked edge tracks its prompt and dies under the other set. A contamination
control (placebo clauses) MEASURES the leak rate of exact exemplar edges -> the mathematical mimicry proof.
"""
import os, sys, json, collections
sys.path.insert(0, "/Users/aflatoongoshtasb/Desktop/NARS/src")

MODEL = os.environ.get("ABLATION_MODEL",
                       "/Users/aflatoongoshtasb/Desktop/NARS/models/qwen2.5-7b-instruct-q4_k_m.gguf")
os.environ["NARS_JARVIS_LLM_GGUF"] = MODEL
from language.llm import LocalLLM
from language.verify_gate import evidence_grounded
from language.consensus import stable_across

GRAMMAR = open(os.path.join(os.path.dirname(__file__),"ontology_edge.gbnf")).read()

SYSTEM = ("You extract RISK relationships a contract clause asserts BETWEEN TWO DISTINCT risk concepts, "
          "as a JSON array of edges. RULES: (1) source and target MUST be two DIFFERENT concepts - never "
          "the same concept twice. (2) Use only relationships the clause actually states; do not infer. "
          "(3) The quote must be copied VERBATIM from the clause. (4) If the clause states only a single "
          "concept with no relation to a second concept, return []. Follow the worked examples' FORM, not "
          "their specific concepts.\n\n")

# Set A and Set B teach the SAME form with DISJOINT node vocabularies (vocab-counterbalanced).
SET_A = ("Examples:\n"
 'Clause: "Vendor shall implement security measures to protect personal data."\n'
 '-> [{"source":"security_obligation","relation":"involves","target":"personal_data","quote":"security measures to protect personal data"}]\n'
 'Clause: "Vendor shall notify Customer of any breach affecting personal data."\n'
 '-> [{"source":"breach_notice","relation":"involves","target":"personal_data","quote":"breach affecting personal data"}]\n'
 'Clause: "This Agreement commences on the Effective Date."\n'
 '-> []\n')
SET_A_EDGES = {("security_obligation", "involves", "personal_data"),
               ("breach_notice", "involves", "personal_data")}

SET_B = ("Examples:\n"
 'Clause: "Customer may audit Vendor records relating to payment."\n'
 '-> [{"source":"audit_rights","relation":"involves","target":"payment_term","quote":"audit Vendor records relating to payment"}]\n'
 'Clause: "Warranty claims shall be resolved by binding arbitration."\n'
 '-> [{"source":"warranty","relation":"triggers","target":"dispute_resolution","quote":"Warranty claims shall be resolved by binding arbitration"}]\n'
 'Clause: "The fee is set out in Schedule A."\n'
 '-> []\n')
SET_B_EDGES = {("audit_rights", "involves", "payment_term"),
               ("warranty", "triggers", "dispute_resolution")}
EXEMPLAR_EDGES = SET_A_EDGES | SET_B_EDGES

# 16 test clauses (same as run 1, for comparability).
CLAUSES = [
 "The Receiving Party shall hold all Confidential Information in strict confidence.",
 "Vendor shall notify Customer within seventy-two (72) hours of any data breach affecting personal data.",
 "The Counterparty may audit and access Customer systems and data on demand.",
 "In no event shall aggregate liability exceed the fees paid in the prior twelve months.",
 "Supplier shall indemnify Buyer against third-party claims arising from Supplier's negligence, but not gross negligence.",
 "Each party shall implement appropriate security measures to protect personal data.",
 "Any sub-processor engaged by Vendor must be approved in writing and is subject to the same data protection obligations.",
 "This Agreement is governed by the laws of the State of Delaware.",
 "Customer may audit Vendor's facilities and records relating to the processing of personal data.",
 "All work product and intellectual property created under this Agreement is assigned to Customer.",
 "The liability cap shall not apply to breaches of confidentiality or data protection obligations.",
 "Vendor must notify Customer of any security incident, which entitles Customer to audit Vendor's systems.",
 "Payment is due within thirty (30) days of invoice.",
 "Neither party is liable for delays caused by events beyond its reasonable control.",
 "Disputes shall be resolved by binding arbitration.",
 "The data breach notification process requires immediate access to affected personal data records.",
]

# Placebo clauses: true content sits in NEITHER exemplar's edge set -> any exemplar edge here is mimicry.
PLACEBO = [
 "This Agreement shall be governed by and construed under the laws of New York.",
 "All intellectual property created hereunder is assigned to the Company.",
 "The initial term of this Agreement is three (3) years.",
 "Either party may terminate this Agreement upon thirty days written notice.",
]

KEY = lambda e: (e.get("source"), e.get("relation"), e.get("target"))


def gen(llm, shots, clause):
    try:
        arr = json.loads(llm.generate_json(SYSTEM + shots, clause, GRAMMAR, max_tokens=400, temperature=0.0))
        return [e for e in arr if isinstance(e, dict)] if isinstance(arr, list) else []
    except Exception:
        return []


def main():
    print(f"loading model: {os.path.basename(MODEL)}", flush=True)
    llm = LocalLLM()

    # --- main pass: exemplar-set invariance + grounding + degeneracy ---
    admitted = []                       # (clause, edge-tuple)
    raw_a = raw_b = deg = ungrounded = 0
    for c in CLAUSES:
        pa, pb = gen(llm, SET_A, c), gen(llm, SET_B, c)
        raw_a += len(pa); raw_b += len(pb)
        invariant = stable_across([pa, pb], KEY)            # survives BOTH primings (decorrelated AND)
        for e in invariant:
            s, r, t = KEY(e)
            if s == t:                                       # degeneracy filter (kills self-loops)
                deg += 1; continue
            if not evidence_grounded(e.get("quote", ""), c): # verbatim grounding
                ungrounded += 1; continue
            admitted.append((c, (s, r, t)))

    # --- contamination control: do exact exemplar edges leak onto off-topic placebo clauses? ---
    contaminated = 0
    leaks = []
    for c in PLACEBO:
        emitted = {KEY(e) for e in gen(llm, SET_A, c)} | {KEY(e) for e in gen(llm, SET_B, c)}
        hit = emitted & EXEMPLAR_EDGES
        if hit:
            contaminated += 1; leaks.append((c, hit))

    uniq = collections.Counter(t for _, t in admitted)
    n = len(CLAUSES)
    print("\n===== ONTOLOGY EDGE-PROPOSAL — ABLATION RESULTS =====")
    print(f"model: {os.path.basename(MODEL)}")
    print(f"clauses: {n}   raw edges (setA/setB): {raw_a}/{raw_b}")
    print(f"[gate] degenerate self-loops filtered: {deg}   ungrounded-quote filtered: {ungrounded}")
    print(f"[YIELD] admitted (grounded & non-degenerate & prompt-invariant): {len(admitted)}")
    print(f"[density] admitted / clause: {len(admitted)/n:.2f}   unique edges: {len(uniq)}   "
          f"unique nodes: {len(set(x for e in uniq for x in (e[0], e[2])))}")
    print("admitted unique edges:")
    for (s, r, t), k in uniq.most_common():
        print(f"  {k}x  {s} --{r}--> {t}")
    print(f"\n[CHALLENGE-1 PROOF] contamination_rate (exemplar edge on placebo): "
          f"{contaminated}/{len(PLACEBO)} = {100*contaminated/len(PLACEBO):.0f}%  (target 0%)")
    for c, hit in leaks:
        print(f"  LEAK on placebo: {hit}  <- '{c[:50]}...'")
    print("[kappa] NOT COMPUTED — no independent labeler (no human; no API key; offline). Unchanged.")
    print("ABLATION-DONE", flush=True)


if __name__ == "__main__":
    main()
