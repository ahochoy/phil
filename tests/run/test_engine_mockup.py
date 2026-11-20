"""The plan's mockup reaches every implementer task (spec 2026-11-20 mockups §4.5)."""

from tests.run.conftest import calc_plan, review, tester_report, write_green, write_red


def scripts():
    return {"implementer": [write_red, write_green], "tester": [tester_report()], "reviewer": [review()]}


def implement_contracts(monkeypatch):
    """Every contract the engine builds an implementer packet from."""
    from phil.run import engine

    seen = []
    real = engine.build_packet

    def spy(role, contract, **kw):
        if role == "implementer":
            seen.append(contract)
        return real(role, contract, **kw)

    monkeypatch.setattr(engine, "build_packet", spy)
    return seen


def test_engine_hands_the_plan_mockup_to_implementers(make_harness, monkeypatch):
    seen = implement_contracts(monkeypatch)
    harness = make_harness(scripts(), plan=calc_plan().model_copy(update={"mockup": "M"}))
    harness.start()
    assert seen and all(contract.mockup == "M" for contract in seen)


def test_a_plan_without_a_mockup_sends_none(make_harness, monkeypatch):
    seen = implement_contracts(monkeypatch)
    harness = make_harness(scripts())
    harness.start()
    assert seen and all(contract.mockup == "" for contract in seen)
