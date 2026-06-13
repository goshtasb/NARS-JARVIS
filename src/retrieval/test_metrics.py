"""ADR-056 §8: the compounding-value telemetry. Salted-canonical-anchor hash + FA-LGR / Stamp-Age /
Flywheel-Close, all content-free and computed at read time."""
from retrieval.metrics import RecallMetrics

DAY = 86400.0


def _m(tmp_path, name="m.db"):
    return RecallMetrics(db_path=str(tmp_path / name))


# ── the salted canonical-anchor hash ──
def test_hash_is_deterministic_and_order_independent(tmp_path):
    m = _m(tmp_path)
    assert m.topic_hash(["solana", "dropped_tx"]) == m.topic_hash(["dropped_tx", "solana"])  # sorted
    assert m.topic_hash(["solana", "solana"]) == m.topic_hash(["solana"])                     # de-duped
    assert m.topic_hash([]) == "" and m.topic_hash(["", " "]) == ""                            # empty -> excluded
    m.close()


def test_hash_collides_for_same_canonical_atoms_distinguishes_different(tmp_path):
    m = _m(tmp_path)
    # two paraphrases that the lexicon resolved to the SAME atoms collide (matching); different atoms don't
    assert m.topic_hash(["solana", "timeout"]) == m.topic_hash(["timeout", "solana"])
    assert m.topic_hash(["solana"]) != m.topic_hash(["ethereum"])
    m.close()


def test_salt_makes_it_irreversible_and_per_install(tmp_path):
    a, b = _m(tmp_path, "a.db"), _m(tmp_path, "b.db")
    # same atoms, DIFFERENT installs (salts) -> different hashes: un-correlatable, un-bruteforceable
    assert a.topic_hash(["solana"]) != b.topic_hash(["solana"])
    # the hash is a SHA-256 hex digest (irreversible form), not the atoms
    h = a.topic_hash(["solana"])
    assert len(h) == 64 and "solana" not in h
    a.close(); b.close()


def test_salt_persists_across_reopen(tmp_path):
    p = str(tmp_path / "m.db")
    m1 = RecallMetrics(db_path=p); h1 = m1.topic_hash(["solana"]); m1.close()
    m2 = RecallMetrics(db_path=p)                                  # reopen -> same salt -> same hash
    assert m2.topic_hash(["solana"]) == h1
    m2.close()


# ── FA-LGR: first-ask grounding rate ──
def test_fa_lgr_uses_first_ask_per_topic(tmp_path):
    m = _m(tmp_path)
    sol = m.topic_hash(["solana"]); eth = m.topic_hash(["ethereum"]); btc = m.topic_hash(["bitcoin"])
    m.record(sol, grounded=False, now=1.0)     # solana: first ask ABSTAINED
    m.record(sol, grounded=True, now=2.0)      # ...later grounded (must NOT flip the first-ask outcome)
    m.record(eth, grounded=True, now=1.0)      # ethereum: first ask grounded
    m.record(btc, grounded=True, now=1.0)      # bitcoin: first ask grounded
    s = m.summary()
    assert s["topics"] == 3 and s["queries"] == 4
    assert abs(s["fa_lgr"] - 2 / 3) < 1e-9     # 2 of 3 topics grounded on FIRST ask (not solana)
    m.close()


def test_zero_anchor_queries_excluded(tmp_path):
    m = _m(tmp_path)
    m.record("", grounded=False)               # zero-anchor ("write a script") -> no-op, not in denominator
    m.record(m.topic_hash(["solana"]), grounded=True, now=1.0)
    s = m.summary()
    assert s["topics"] == 1 and s["fa_lgr"] == 1.0
    m.close()


# ── Stamp-Age Depth ──
def test_stamp_age_median_over_grounded(tmp_path):
    m = _m(tmp_path)
    m.record(m.topic_hash(["a"]), grounded=True, stamp_age_days=5.0, now=1.0)
    m.record(m.topic_hash(["b"]), grounded=True, stamp_age_days=15.0, now=1.0)
    m.record(m.topic_hash(["c"]), grounded=True, stamp_age_days=40.0, now=1.0)
    m.record(m.topic_hash(["d"]), grounded=False, now=1.0)         # abstain has no age -> ignored
    assert m.summary()["stamp_age_median_days"] == 15.0
    m.close()


# ── Flywheel Close Rate ──
def test_flywheel_close_within_window(tmp_path):
    m = _m(tmp_path)
    h1 = m.topic_hash(["packets"]); h2 = m.topic_hash(["zkrollup"]); h3 = m.topic_hash(["staking"])
    m.record(h1, grounded=False, now=0.0); m.record(h1, grounded=True, now=3 * DAY)   # closed (3d <= 7d)
    m.record(h2, grounded=False, now=0.0); m.record(h2, grounded=True, now=10 * DAY)  # too late (>7d) -> open
    m.record(h3, grounded=False, now=0.0)                                              # never re-resolved -> open
    s = m.summary()
    assert abs(s["flywheel_close_rate"] - 1 / 3) < 1e-9            # 1 of 3 abstained topics closed in window
    m.close()


def test_empty_summary(tmp_path):
    s = _m(tmp_path).summary()
    assert s["queries"] == 0 and s["fa_lgr"] is None and s["flywheel_close_rate"] is None


def test_trend_period_over_period(tmp_path):
    """ADR-056 §8: trend() reports FA-LGR over topics FIRST asked in the current vs prior 30 days, each
    with its N for the cold-start gate. Compounding shows as a rising current rate."""
    m = RecallMetrics(str(tmp_path / "m.db"))
    now = 1_000_000.0
    day = 86400.0
    for i in range(5):                                   # prior 30d window: 2/5 grounded
        m.record(m.topic_hash([f"old{i}"]), grounded=(i < 2), now=now - 45 * day)
    for i in range(4):                                   # current 30d window: 3/4 grounded
        m.record(m.topic_hash([f"new{i}"]), grounded=(i < 3), now=now - 5 * day)
    t = m.trend(now)
    assert t["prior_n"] == 5 and abs(t["prior_fa_lgr"] - 0.4) < 1e-9
    assert t["current_n"] == 4 and abs(t["current_fa_lgr"] - 0.75) < 1e-9
    m.close()


def test_trend_empty_is_none(tmp_path):
    m = RecallMetrics(str(tmp_path / "m.db"))
    t = m.trend(1_000_000.0)
    assert t == {"current_fa_lgr": None, "current_n": 0, "prior_fa_lgr": None, "prior_n": 0}
    m.close()
