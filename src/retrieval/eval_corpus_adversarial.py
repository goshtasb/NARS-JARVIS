"""Gate 2.2 / 2.3 — the Planted Adversarial Matrix (ADR-056 §6.2).

Tightly controlled Narsese worlds with ground-truth labels (load-bearing vs decoy vs distractor) knowable
BY CONSTRUCTION. Each Case names the chain that must derive the answer, the noise that must be excluded
from the STAMP, and the targets (chain endpoints) Stage 3 pins so a budget flood can't evict a deep link.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from retrieval.traversal import Belief

NOW = 1_000_000.0
DAY = 86400.0


def _b(narsese: str, f: float = 1.0, c: float = 0.9, age_days: float = 10.0) -> Belief:
    return Belief(narsese, f, c, NOW - age_days * DAY)


@dataclass(frozen=True)
class Case:
    name: str
    query: str                       # the human query
    question: str                    # the Narsese question ONA answers
    anchors: list[str]               # Stage-1 resolved anchors (entities)
    targets: set[str]                # chain endpoints Stage 3 pins (from the query's other content terms)
    beliefs: list[Belief]            # the full planted world
    load_bearing: set[str]           # narsese that must derive the answer (recall + STAMP)
    decoys: set[str]                 # adjacency-retrievable, wrong branch -> must NOT be in the STAMP
    distractors: set[str] = field(default_factory=set)   # unrelated, high conf/recent -> must NOT retrieve


# ── Case 0: baseline — "Why did my tx drop on SOL?" ──
_C0_LOAD = ["<solana --> timeout>", "<timeout --> dropped_tx>"]
_C0_DECOY = ["<solana --> has_token>", "<solana --> staking>", "<timeout --> log_entry>"]
SOLANA_CASE = Case(
    name="solana_tx_drop", query="Why did my tx drop on SOL?", question="<solana --> dropped_tx>?",
    anchors=["solana"], targets={"dropped_tx"},
    beliefs=([_b(n) for n in _C0_LOAD] + [_b(n) for n in _C0_DECOY] + [_b("<solana --> blockchain>")]
             + [_b(n, c=0.99, age_days=0.0) for n in ("<cursor --> editor>", "<chrome --> browser>")]),
    load_bearing=set(_C0_LOAD), decoys=set(_C0_DECOY),
    distractors={"<cursor --> editor>", "<chrome --> browser>"},
)

# ── Case 1: Ambiguous Anchor Collision — 'mac' is BOTH a network interface and Apple hardware ──
_C1_LOAD = ["<mac --> network_interface>", "<network_interface --> packet_loss>"]
_C1_DECOY = ["<mac --> retina_display>", "<mac --> m2_chip>", "<mac --> high_cpu>"]   # Apple subgraph
AMBIGUOUS_CASE = Case(
    name="ambiguous_mac", query="Why is my MAC dropping packets?", question="<mac --> packet_loss>?",
    anchors=["mac"], targets={"packet_loss"},
    beliefs=([_b(n) for n in _C1_LOAD] + [_b(n, c=0.99, age_days=0.0) for n in _C1_DECOY]),  # Apple = confident+recent
    load_bearing=set(_C1_LOAD), decoys=set(_C1_DECOY),
)

# ── Case 2: Temporal Contradiction — the user corrected where the key lives ──
_C2_LOAD = ["<api_key --> in_keychain>"]
_C2_OBSOLETE = "<api_key --> in_config>"
REVISION_CASE = Case(
    name="revision_api_key", query="Where is my API key now?", question="<api_key --> in_keychain>?",
    anchors=["api_key"], targets={"in_keychain"},
    beliefs=[
        _b(_C2_OBSOLETE, f=1.0, c=0.9, age_days=60.0),     # Day 1: "it's in the config file"
        _b(_C2_OBSOLETE, f=0.0, c=0.9, age_days=0.0),      # Day 60 CORRECTION: "no, not in config" -> revise
        _b("<api_key --> in_keychain>", f=1.0, c=0.9, age_days=0.0),   # Day 60: "it's in the keychain"
    ],
    load_bearing=set(_C2_LOAD), decoys={_C2_OBSOLETE},
)

# ── Case 3: Saturated Context Budget — a 4-hop chain vs a flood of confident, recent 1-hop decoys ──
_C3_CHAIN = ["<router --> firmware_v2>", "<firmware_v2 --> buffer_bug>",
             "<buffer_bug --> mem_overflow>", "<mem_overflow --> packet_loss>"]
_C3_DECOYS = [f"<router --> d{i:02d}>" for i in range(15)]
SATURATED_CASE = Case(
    name="saturated_budget", query="Why is my router losing packets?", question="<router --> packet_loss>?",
    anchors=["router"], targets={"packet_loss"},
    beliefs=([_b(n) for n in _C3_CHAIN] + [_b(n, f=1.0, c=0.99, age_days=0.0) for n in _C3_DECOYS]),
    load_bearing=set(_C3_CHAIN), decoys=set(_C3_DECOYS),
)

CASES = [SOLANA_CASE, AMBIGUOUS_CASE, REVISION_CASE, SATURATED_CASE]
