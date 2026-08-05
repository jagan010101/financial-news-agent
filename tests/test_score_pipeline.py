"""Scoring pipeline tests: composite, rule-floor override, judge-error fallback,
and premium dispute resolution.

These use in-memory stubs for the judge(s) and exercise score_article_holding
directly with a fake session that records added objects — no DB needed.
"""
import pytest

from finrag.score.judge import JudgeError
import finrag.score.pipeline as P


class FakeSession:
    def __init__(self): self.added = []
    def add(self, obj): self.added.append(obj)
    def get(self, model, pk): return None  # Source/ArticleHolding lookups; code handles None
    def execute(self, *a, **k):
        class _R:
            def all(self_): return []
        return _R()


class Art:
    def __init__(self, title, body=""):
        self.id, self.title, self.body = 1, title, body
        self.embedding, self.published_at, self.source_id = None, None, 3


class Hold:
    id, common_name, nse_symbol, sector = 1, "HDFC Bank", "HDFCBANK", "Banking"


class GoodJudge:
    name = "test/good"
    def score(self, s, u):
        return dict(direct_relevance=9, materiality=8, urgency=6, credibility=9,
                    event_type="earnings_result", rationale="beat")


class LowballJudge:
    name = "test/lowball"
    def score(self, s, u):
        return dict(direct_relevance=2, materiality=1, urgency=1, credibility=2,
                    event_type="routine_disclosure", rationale="meh")


class BrokenJudge:
    name = "test/broken"
    def score(self, s, u): raise JudgeError("outage")


class BadEventJudge:
    """Passes schema but uses an unrecognized event_type -> validate_score flags it."""
    name = "test/bad-event"
    def score(self, s, u):
        return dict(direct_relevance=8, materiality=7, urgency=5, credibility=7,
                    event_type="mystery_event", rationale="unclear")


class PremiumJudge:
    name = "premium/test-model"
    def __init__(self, dims=None, raises=False):
        self._dims = dims or dict(direct_relevance=9, materiality=8, urgency=6,
                                   credibility=9, event_type="earnings_result",
                                   rationale="premium verdict")
        self._raises = raises
    def score(self, s, u):
        if self._raises:
            raise RuntimeError("premium outage")
        return self._dims


def test_normal_scoring_composite():
    sc = P.score_article_holding(
        FakeSession(), Art("HDFC Bank Q1 profit rises"), Hold(),
        GoodJudge(), embed_fn=None)
    assert float(sc.composite) == 8.2
    assert sc.event_type == "earnings_result"
    assert sc.model and sc.prompt_version


def test_rule_floor_overrides_lowball_judge():
    # judge says routine, but SEBI+fraud text must floor to 9
    sc = P.score_article_holding(
        FakeSession(),
        Art("SEBI order: forensic audit alleges fraud at unit"),
        Hold(), LowballJudge(), embed_fn=None)
    assert float(sc.composite) == 9.0
    assert sc.rule_floor == 9
    assert sc.event_type == "fraud_disclosure"


def test_judge_error_does_not_crash_and_is_recorded():
    sc = P.score_article_holding(
        FakeSession(), Art("Some routine news"), Hold(),
        BrokenJudge(), embed_fn=None)
    assert sc._judge_failed is True
    assert "judge_error" in sc.rationale


def test_judge_error_still_allows_rule_floor():
    # broken judge but fraud text -> floor still protects
    sc = P.score_article_holding(
        FakeSession(),
        Art("SEBI passes order on fraud at company"),
        Hold(), BrokenJudge(), embed_fn=None)
    assert float(sc.composite) == 9.0
    assert sc._judge_failed is True


# --- premium dispute resolution --------------------------------------------

def test_no_escalation_without_premium_judge():
    """premium_judge=None (the default) means a flagged row just stays flagged —
    matches every pre-existing caller/test that never passes premium_judge."""
    sc = P.score_article_holding(
        FakeSession(), Art("Some routine news"), Hold(),
        BadEventJudge(), embed_fn=None)
    assert sc.validation_status == "flagged"
    assert "event_type_unrecognized" in sc.flag_reasons
    assert sc.event_type == "mystery_event"


def test_premium_verdict_overrides_flagged_row(monkeypatch):
    logged = {}
    monkeypatch.setattr(P, "record_dispute", lambda **kw: logged.update(kw))

    premium = PremiumJudge()
    sc = P.score_article_holding(
        FakeSession(), Art("Some routine news"), Hold(),
        BadEventJudge(), embed_fn=None, premium_judge=premium)

    assert sc.validation_status == "premium_resolved"
    assert sc.event_type == "earnings_result"          # premium's event_type, not the normal judge's
    assert float(sc.composite) == 8.2                   # composite_score of premium's dims
    assert sc.model == "premium/test-model"             # model tag reflects the FINAL judge
    assert "premium-resolved" in sc.rationale

    assert logged["article_id"] == 1
    assert logged["holding_id"] == 1
    assert logged["dispute_reasons"] == ["event_type_unrecognized"]
    assert logged["premium_provider"] == "premium/test-model"
    assert logged["final_event_type"] == "earnings_result"


def test_premium_failure_leaves_row_flagged(monkeypatch):
    called = []
    monkeypatch.setattr(P, "record_dispute", lambda **kw: called.append(kw))

    sc = P.score_article_holding(
        FakeSession(), Art("Some routine news"), Hold(),
        BadEventJudge(), embed_fn=None, premium_judge=PremiumJudge(raises=True))

    assert sc.validation_status == "flagged"            # unchanged: premium call failed
    assert sc.event_type == "mystery_event"              # normal judge's verdict retained
    assert called == []                                  # nothing logged — no dispute was resolved


def test_premium_not_consulted_when_passed():
    """A clean, passing row never calls the premium judge at all.

    credibility must stay <=8 here: with FakeSession returning no Source row,
    authority_rank defaults to 4, and validate_score flags
    credibility_authority_mismatch when authority>=4 and credibility>=9 (this
    is why GoodJudge, which uses credibility=9, is itself flagged — see
    test_premium_verdict_overrides_flagged_row for that path instead)."""
    class CleanJudge:
        name = "test/clean"
        def score(self, s, u):
            return dict(direct_relevance=8, materiality=7, urgency=5, credibility=7,
                        event_type="earnings_result", rationale="clean")

    class ExplodingIfCalled:
        name = "premium/should-not-run"
        def score(self, s, u): raise AssertionError("premium judge should not be called")

    sc = P.score_article_holding(
        FakeSession(), Art("HDFC Bank Q1 profit rises"), Hold(),
        CleanJudge(), embed_fn=None, premium_judge=ExplodingIfCalled())
    assert sc.validation_status == "passed"
