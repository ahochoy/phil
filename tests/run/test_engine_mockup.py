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


def test_the_tester_and_reviewer_are_not_sent_the_mockup(make_harness, monkeypatch):
    from phil.run import engine

    seen = {}
    real = engine.build_packet

    def spy(role, contract, **kw):
        seen.setdefault(role, []).append(contract)
        return real(role, contract, **kw)

    monkeypatch.setattr(engine, "build_packet", spy)
    harness = make_harness(scripts(), plan=calc_plan().model_copy(update={"mockup": "M"}))
    harness.start()
    assert seen["tester"] and all(contract.plan.mockup == "" for contract in seen["tester"])
    assert seen["reviewer"] and all(contract.plan.mockup == "" for contract in seen["reviewer"])


def test_a_mockup_that_overflows_a_small_budget_is_dropped(make_harness):
    from phil.config import PhilConfig, RoleBudget

    config = PhilConfig(budget={"implementer": RoleBudget(max_input_tokens=1_500)})
    harness = make_harness(scripts(), plan=calc_plan().model_copy(update={"mockup": "m" * 10_000}), config=config)
    final = harness.start()  # doesn't raise PacketTooLarge
    assert final["status"] == "completed"
    payloads = [p for role, p in harness.factory.calls if role == "implementer"]
    assert payloads
    for payload in payloads:
        content = str(payload["messages"][0]["content"])
        assert "m" * 1000 not in content
        assert "def add(a, b)" in content  # the task's files_hint file still fits


def test_a_plan_without_a_mockup_sends_none(make_harness, monkeypatch):
    seen = implement_contracts(monkeypatch)
    harness = make_harness(scripts())
    harness.start()
    assert seen and all(contract.mockup == "" for contract in seen)
