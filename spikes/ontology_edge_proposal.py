import os, sys, json, collections
sys.path.insert(0,"/Users/aflatoongoshtasb/Desktop/NARS/src")
os.environ["NARS_JARVIS_LLM_GGUF"]="/Users/aflatoongoshtasb/Desktop/NARS/models/qwen2.5-7b-instruct-q4_k_m.gguf"
from language.llm import LocalLLM
from language.verify_gate import evidence_grounded
from language.consensus import stable_across
GRAMMAR=open(os.path.join(os.path.dirname(__file__),"ontology_edge.gbnf")).read()
PROMPT=("Extract the RISK-BEARING relationships this contract clause STATES, as a JSON array of edges "
        "between risk concepts from the allowed vocabulary. Use only relationships the clause actually "
        "asserts. The quote must be copied verbatim from the clause. Do not infer beyond the text. None -> [].")
CLAUSES=[
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
print(f"loading 7B; {len(CLAUSES)} clauses x 3-pass consensus", flush=True)
llm=LocalLLM()
def propose(c):
    passes=[]
    for t in (0.0,0.3,0.6):
        try: arr=json.loads(llm.generate_json(PROMPT,c,GRAMMAR,max_tokens=400,temperature=t))
        except Exception: arr=[]
        passes.append([e for e in arr if isinstance(e,dict)] if isinstance(arr,list) else [])
    return stable_across(passes, lambda e:(e.get("source"),e.get("relation"),e.get("target")))

raw=0; grounded=[]; ungrounded=0; parse_fail=0
for c in CLAUSES:
    edges=propose(c)
    for e in edges:
        raw+=1
        if evidence_grounded(e.get("quote",""), c): grounded.append((e["source"],e["relation"],e["target"]))
        else: ungrounded+=1
uniq=collections.Counter(grounded)
nclause=len(CLAUSES)
print("\n===== ONTOLOGY EDGE-PROPOSAL SPIKE — RESULTS =====")
print(f"clauses: {nclause}")
print(f"[raw] stable consensus edge-instances proposed: {raw}")
print(f"[grounding] verbatim-quote PASS: {len(grounded)}  FAIL(dropped/hallucinated quote): {ungrounded}"
      + f"  -> grounding false-edge floor = {100*ungrounded/max(1,raw):.0f}%")
print(f"[1 dedup] unique edges: {len(uniq)}  | dedup ratio (instances/unique): {len(grounded)/max(1,len(uniq)):.2f}")
tail=[k for k,v in uniq.items() if v==1]
print(f"[1 long-tail] single-occurrence unique edges: {len(tail)} / {len(uniq)} ({100*len(tail)/max(1,len(uniq)):.0f}% of graph)")
print(f"[2 density] grounded edges / clause: {len(grounded)/nclause:.2f}  | unique nodes used: {len(set(n for e in uniq for n in (e[0],e[2])))}")
print("\nunique edges (count):")
for (s,r,t),n in uniq.most_common():
    print(f"  {n}x  {s} --{r}--> {t}")
print("\n[3 precision/false-edge] grounding floor above is OBJECTIVE; legal-correctness precision needs a human gold (unavailable).")
print("[4 kappa] NOT COMPUTED — no independent second labeler (no human; no API key for a frontier proxy; offline).")
print("SPIKE2-DONE", flush=True)
