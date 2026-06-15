import os, sys, math
sys.path.insert(0, "/Users/aflatoongoshtasb/Desktop/NARS/src")

# ---------- dummy corpus: 20 contracts, each a set of clause types + representative clause texts ----------
STD = ["confidentiality","term_and_termination","indemnification","breach_notification","liability_cap","governing_law"]
CORPUS_CLAUSE_TEXTS = {
 "confidentiality":"The Receiving Party shall hold all Confidential Information in strict confidence.",
 "term_and_termination":"This Agreement continues for two years; either party may terminate on 30 days notice.",
 "indemnification":"Each party shall indemnify the other against third-party claims from its negligence.",
 "breach_notification":"Vendor shall notify Customer within seventy-two (72) hours of any data breach.",
 "liability_cap":"In no event shall aggregate liability exceed the fees paid in the prior twelve months.",
 "governing_law":"This Agreement is governed by the laws of the State of Delaware.",
}
# 20 contracts all carry the 6 standard clauses
corpus = {f"contract_{i:02d}": set(STD) for i in range(1,21)}

# the NEW contract under review: MISSING liability_cap, and has a NOVEL 'audit_rights' clause
NEW_CLAUSES = {
 "confidentiality": CORPUS_CLAUSE_TEXTS["confidentiality"],
 "term_and_termination": CORPUS_CLAUSE_TEXTS["term_and_termination"],
 "breach_notification": "Seller must notify Buyer of any Data Breach within twenty-four (24) hours.",
 "governing_law": CORPUS_CLAUSE_TEXTS["governing_law"],
 "audit_rights": "The Counterparty may audit and access Customer systems and data on demand.",  # NOVEL
}  # note: NO liability_cap (omission), NO indemnification

print("="*70); print("BASELINE: embeddings (nomic) + cosine similarity"); print("="*70, flush=True)
from llama_cpp import Llama
emb = Llama(model_path="/Users/aflatoongoshtasb/Desktop/NARS/models/nomic-embed-text-v1.5.f16.gguf",
            embedding=True, n_ctx=512, verbose=False)
def vec(t):
    e = emb.embed(t)
    return e[0] if isinstance(e[0], list) else e
def cos(a,b):
    d=sum(x*y for x,y in zip(a,b)); na=math.sqrt(sum(x*x for x in a)); nb=math.sqrt(sum(x*x for x in b))
    return d/(na*nb+1e-9)
corpus_vecs = {ct: vec(txt) for ct,txt in CORPUS_CLAUSE_TEXTS.items()}   # one centroid per known clause type
# Test A — NOVELTY: the audit clause vs every known corpus clause type
av = vec(NEW_CLAUSES["audit_rights"])
sims = {ct: cos(av, cv) for ct,cv in corpus_vecs.items()}
best = max(sims, key=sims.get)
print(f"[A NOVELTY] 'audit_rights' max cosine to any known clause = {sims[best]:.3f} (closest: {best})")
print(f"           -> {'FLAGGED novel (below 0.6)' if sims[best]<0.6 else 'NOT flagged (looks familiar)'}")
# Test B — OMISSION: is any new-contract clause near the liability_cap centroid?
capv = corpus_vecs["liability_cap"]
covered = max(cos(vec(t), capv) for t in NEW_CLAUSES.values())
print(f"[B OMISSION] best match of any NEW clause to liability_cap centroid = {covered:.3f}")
print(f"           -> {'FLAGGED missing liability_cap (<0.6)' if covered<0.6 else 'present-ish'}")
# Test C — CONNECT THE DOTS: can embeddings infer audit_rights => data-access RISK?
riskv = vec("clause creating data access security risk exposure")
print(f"[C COMPOSE] cosine('audit_rights', 'data access risk') = {cos(av, riskv):.3f}")
print( "           -> embeddings can only report similarity; they cannot LOGICALLY derive 'risk' as a conclusion.")

print(); print("="*70); print("CHALLENGER: NARS / ONA"); print("="*70, flush=True)
from brain import Brain
b = Brain(cycles_per_step=200)
try:
    # firm ontology (the legal knowledge graph) + corpus facts
    b.add_belief("<audit_clause --> data_access_clause>.")        # audit rights ARE data-access
    b.add_belief("<data_access_clause --> high_risk_clause>.")    # data-access clauses ARE high-risk
    # the new contract's audit clause is an audit_clause
    b.add_belief("<new_audit_clause --> audit_clause>.")
    # Test C — composition / connect-the-dots
    ansC = b.ask("<new_audit_clause --> high_risk_clause>?")
    print(f"[C COMPOSE] ask <new_audit_clause --> high_risk_clause>? -> {ansC.term if ansC else None}"
          + (f"  truth f={ansC.truth.frequency:.2f},c={ansC.truth.confidence:.2f}  stamp={ansC.stamp}" if ansC and ansC.truth else "  (no derivation)"))
    if ansC and ansC.stamp:
        print(f"           provenance (beliefs that combined): {b.evidence_terms(ansC.stamp)}")
    # Test B — omission via induction: 20 contracts have liability_cap; expect it for a contract
    for i in range(1,21):
        b.add_belief(f"<c{i:02d} --> contract>.")
        b.add_belief(f"<c{i:02d} --> [has_liability_cap]>.")
    b.add_belief("<cNEW --> contract>.")
    ansB = b.ask("<cNEW --> [has_liability_cap]>?")
    print(f"[B OMISSION] ask <cNEW --> [has_liability_cap]>? -> {ansB.term if ansB else None}"
          + (f"  truth f={ansB.truth.frequency:.2f},c={ansB.truth.confidence:.2f}" if ansB and ansB.truth else "  (no expectation induced)"))
    print( "           (a high-confidence EXPECTED 'has_liability_cap' that the doc does NOT assert = inductive omission signal)")
    # Test A — novelty in NARS: ask what the audit clause relates to (open-world)
    ansA = b.ask("<new_audit_clause --> ?x>?")
    print(f"[A NOVELTY] ask <new_audit_clause --> ?x>? -> {ansA.term if ansA else None}"
          + (f"  f={ansA.truth.frequency:.2f},c={ansA.truth.confidence:.2f}" if ansA and ansA.truth else ""))
finally:
    b.close()
print("\nSPIKE-DONE", flush=True)
