# Jev Revisit (Plan B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rerun the Jev classifier benchmark under the revised decision rule agreed before the run, with each classifier's thresholds tuned on the existing 40 cases and checked on about 10 new ones.

**Architecture:**
- **Routing:** gains a per-classifier detail threshold. `routing.jev_detail_threshold` overrides `routing.detail_threshold` for Jev's judgements.
- **Benchmark metrics** (`tests/live/bench/classify/metrics.py`):
  - count wrong paths, unsafe errors, deferrals and missed detail separately;
  - sweep both thresholds;
  - choose each backend's thresholds by a fixed rule.
- **Report:** chooses thresholds on the `tune` split, shows the `check` split at those thresholds, and applies the new decision rule (spec §4.1) to all cases.
- **Cases:** `f-01` is relabelled with a recorded reason. About 10 `check` cases are added, among them the CTA and pricing requests.

**Tech Stack:** Python 3.14, pydantic v2, pytest (offline only).

**Spec:** `docs/superpowers/specs/2026-10-03-phil-ask-before-acting-design.md` §4. It replaces the decision rule in `docs/superpowers/specs/2026-09-30-phil-m3-proportional-orchestration-design.md` §5.1.

## Global Constraints

- **Editing files:** use only the Edit or Write tools. Never edit through python, perl, sed, heredocs or printf in Bash.
- **Commits:**
  - write the message to a file with Write, then `git commit -F <file>`;
  - the message ends with a blank line and then exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Bash command lines:** keep the words "keychain" and "credentials" out of them.
- **Hooks and guards:** never work around one. If you are blocked, stop and report BLOCKED.
- **Secrets:** never read or print `.env` files or key values.
- **Tests:**
  - never run `-m live` or `-m bench`; the user runs the live benchmark;
  - iterate with `uv run pytest <paths> -q -n 0`;
  - run the full `uv run pytest -q` once, at the end of each task.
- **Decision rule (spec §4.1).** Jev is worth recommending over `llm` if all five hold:
  1. **Wrong-path errors:** Jev's count is no more than llm's + 1. A wrong path is a routed depth that differs from the expected depth, excluding intake.
  2. **Unsafe errors:** Jev has no more `answer_as_change` plus `full_as_quick` than llm.
  3. **Deferrals:** Jev's intake count is no more than llm's + 2.
  4. **Latency:** Jev's p95 is at most a third of llm's.
  5. **Cost:** Jev's cost per 100 requests is lower. Unknown cost means `undecided (cost unknown)`, unless a known condition fails, which means `no`.
- **Thresholds (spec §4.2):**
  - `routing.detail_threshold` stays the default (0.6);
  - `routing.jev_detail_threshold` (default `None`) overrides it for Jev only;
  - both thresholds are swept over 0.3–0.9 in steps of 0.1;
  - each backend's thresholds are chosen on the `tune` split, then shown on the `check` split.
- **Threshold choice rule (this plan's ruling R1):** over the grid, pick the row that minimises, in order:
  1. `unsafe`;
  2. `wrong_path`;
  3. `missed_detail`;
  4. `intake_count`;
  5. the distance `|confidence − 0.5| + |detail − 0.6|` from today's defaults.

  If those tie, take the lower confidence threshold, then the lower detail threshold.
- **Labels win over stored records.** The report takes `expected_class`, `expected_depth`, `ambiguous` and `split` from `cases.jsonl` by `case_id`, not from the stored record. A relabelled case is then rescored without a rerun.

## Review Focus

1. **A missing `split` field:** old records, and cases written without it, count as `tune` and must not crash the report. Tested in Task 3.
2. **A stored record whose case no longer exists:** it is left out of the report, not crashed on. Tested in Task 3.
3. **An unavailable router** (judgement `None`) with `jev_detail_threshold` set: the default threshold applies and routing defers to intake as before. Tested in Task 1.
4. **A grid row that ties on every count:** the choice is deterministic: closest to the defaults, then the lower thresholds. Tested in Task 2.
5. **A backend with records only in `check`:** the report says it has no `tune` results instead of choosing from nothing. Tested in Task 3.

## Rulings made while planning

- **R1, the threshold choice rule:** as in Global Constraints. It puts safety first (`unsafe`), then wrong paths, then vague requests that weren't asked about, then fewer deferrals.
- **R2, `f-01` is relabelled `simple_change` / `quick`.** "Add a multiply function to calc with tests, matching how add and subtract are done" follows an existing pattern in one file. The M3b end-to-end benchmark (M3 spec §5.2) already expects its twin, `py-multiply`, to take the quick path. Part 3 called the old label debatable. The case keeps its id and gains a `note` with this reason.
- **R3, `missed_detail`** is the number of `ambiguous` cases not routed to intake. It informs the threshold choice and the report, not the decision rule, which stays as spec §4.1 wrote it.

---

### Task 1: A per-classifier detail threshold in routing

**Files:**
- Modify: `src/phil/config.py`. `RoutingConfig` gets `jev_detail_threshold` and `detail_threshold_for`.
- Modify: `src/phil/chat/controller.py:659-665`. `_on_route_ready` uses `detail_threshold_for`.
- Modify: `tests/live/bench/harness.py:222-226`. The end-to-end harness uses it too.
- Test: `tests/test_config.py`, `tests/chat/test_routing_flow.py`

**Interfaces:**
- Produces: `RoutingConfig.jev_detail_threshold: float | None = None` and `RoutingConfig.detail_threshold_for(source: str | None) -> float`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_jev_gets_its_own_detail_threshold_when_set():
    routing = PhilConfig.model_validate({"routing": {"jev_detail_threshold": 0.5}}).routing
    assert routing.detail_threshold_for("jev") == 0.5
    assert routing.detail_threshold_for("llm") == 0.6
    assert routing.detail_threshold_for(None) == 0.6  # router unavailable: the default


def test_without_a_jev_threshold_every_source_uses_the_default():
    routing = PhilConfig().routing
    assert routing.jev_detail_threshold is None
    assert routing.detail_threshold_for("jev") == routing.detail_threshold == 0.6


def test_the_jev_detail_threshold_is_a_probability():
    with pytest.raises(ValueError):
        PhilConfig.model_validate({"routing": {"jev_detail_threshold": 1.2}})
```

Append to `tests/chat/test_routing_flow.py`. The `route()` helper builds a `RouteJudgement`. Check how the existing tests make the router's source `jev`. If `ChatFactory` scripts only the LLM backend, monkeypatch `controller_mod.classify` to return a `Classification` whose `judgement` is a `Judgement(source="jev", ...)`, as `test_router_unavailable_line_when_jev_fails` does with its monkeypatches:

```python
def test_jev_judgements_use_the_jev_detail_threshold(calc_repo, monkeypatch):
    judgement = Judgement(
        task_class="simple_change", probabilities={"simple_change": 0.9}, confidence=0.9, needs_detail=0.55,
        source="jev", latency_ms=5, usage=None,
    )
    monkeypatch.setattr(controller_mod, "classify", lambda *a, **k: Classification(judgement, None))
    config = PhilConfig(models=TEST_MODELS, routing={"jev_detail_threshold": 0.5})
    text, *_ = run_chat(calc_repo, ["fix the page", peek({})], {"intake": [goal(open_questions=["Which page?"])]}, config=config)
    assert "Unclear request · asking first" in text  # 0.55 >= Jev's 0.5, though below the default 0.6
```

Adjust the `Classification` constructor call to its real signature in `phil/routing/classify.py`; read it first.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/chat/test_routing_flow.py -q -n 0 -k "detail_threshold or jev_judgements"`
Expected: FAIL with `jev_detail_threshold` unknown.

- [ ] **Step 3: Implement**

In `src/phil/config.py` `RoutingConfig`, add the field after `detail_threshold`, include it in the probability validator, and add the method:

```python
    # Jev scores "is this vague?" on its own scale (journal part 3): it gets its own threshold.
    jev_detail_threshold: float | None = None

    @field_validator("confidence_threshold", "detail_threshold", "jev_detail_threshold")
    @classmethod
    def _validate_probability(cls, value: float | None) -> float | None:
        if value is not None and not 0 <= value <= 1:
            raise ValueError("routing thresholds must be between 0 and 1")
        return value

    def detail_threshold_for(self, source: str | None) -> float:
        """The needs-detail threshold for a judgement from `source` ("jev", "llm", or None when
        the router was unavailable)."""
        if source == "jev" and self.jev_detail_threshold is not None:
            return self.jev_detail_threshold
        return self.detail_threshold
```

This replaces the existing `_validate_probability`.

In `src/phil/chat/controller.py` `_on_route_ready`, change `detail_threshold=self.config.routing.detail_threshold,` to `detail_threshold=self.config.routing.detail_threshold_for(judgement.source if judgement else None),`.

In `tests/live/bench/harness.py`, make the same change, with `ctx.config.routing`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/chat/test_routing_flow.py tests/routing tests/live/bench/test_harness_offline.py -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`. Commit message: `Give Jev its own needs-detail threshold`, plus the trailer.

---

### Task 2: Benchmark metrics: separate counts, a 2-D sweep and a threshold choice

**Files:**
- Modify: `tests/live/bench/classify/metrics.py`
- Test: `tests/live/bench/classify/test_metrics_offline.py`

**Interfaces:**
- Produces:
  - `summarise(...)` also returns `"wrong_path": int`, `"unsafe": int`, `"intake_count": int` and `"missed_detail": int`;
  - `sweep_grid(records, confidence_grid=DEFAULT_THRESHOLDS, detail_grid=DEFAULT_THRESHOLDS) -> list[dict]`, where each row has `confidence_threshold`, `detail_threshold`, `depth_accuracy`, `wrong_path`, `unsafe`, `intake_count` and `missed_detail`;
  - `choose_thresholds(records, confidence_grid=DEFAULT_THRESHOLDS, detail_grid=DEFAULT_THRESHOLDS) -> dict`, which returns the chosen row.

- [ ] **Step 1: Write the failing tests**

In `test_summarise_every_field`, add these keys to the expected dict. They're derived from the fixture's `confusion`:

```python
        "wrong_path": 2,  # q-02 answer->quick, f-01 full->answer (intake never counts)
        "unsafe": 1,  # answer_as_change 1 + full_as_quick 0
        "intake_count": 3,  # v-01, o-01, x-01
        "missed_detail": 0,  # the one ambiguous case (v-01) went to intake
```

In `test_summarise_with_no_records`, if it asserts a full dict, add the same four keys with value `0`.

Append:

```python
def _case(case_id, expected_depth, task_class, *, confidence=0.9, needs_detail=0.1, ambiguous=False):
    return {
        "case_id": case_id, "backend": "jev", "expected_class": task_class, "expected_depth": expected_depth,
        "ambiguous": ambiguous, "task_class": task_class, "probabilities": {task_class: confidence},
        "confidence": confidence, "needs_detail": needs_detail, "latency_ms": 10, "input_tokens": 1,
        "output_tokens": 0, "cost_usd": 0.0, "error": None,
    }


def test_a_vague_case_routed_straight_through_is_missed_detail_not_a_wrong_path():
    records = [_case("v-1", "full", "feature", needs_detail=0.55, ambiguous=True)]
    summary = metrics.summarise(records, confidence_threshold=0.5, detail_threshold=0.6)
    assert (summary["wrong_path"], summary["missed_detail"], summary["intake_count"]) == (0, 1, 0)


def test_sweep_grid_covers_both_thresholds():
    rows = metrics.sweep_grid(RECORDS, confidence_grid=(0.5, 0.9), detail_grid=(0.6, 0.7))
    assert [(r["confidence_threshold"], r["detail_threshold"]) for r in rows] == [(0.5, 0.6), (0.5, 0.7), (0.9, 0.6), (0.9, 0.7)]
    assert set(rows[0]) == {
        "confidence_threshold", "detail_threshold", "depth_accuracy", "wrong_path", "unsafe", "intake_count",
        "missed_detail",
    }


def test_choose_thresholds_puts_safety_first_then_wrong_paths_then_vague_requests():
    # A vague request scored 0.55: only a detail threshold of 0.5 or less sends it to intake.
    # A clear change scored 0.45: a detail threshold of 0.4 or less wrongly defers it.
    records = [
        _case("v-1", "full", "feature", needs_detail=0.55, ambiguous=True),
        _case("s-1", "quick", "simple_change", needs_detail=0.45),
    ]
    chosen = metrics.choose_thresholds(records, confidence_grid=(0.5,), detail_grid=(0.4, 0.5, 0.6))
    assert (chosen["confidence_threshold"], chosen["detail_threshold"]) == (0.5, 0.5)


def test_choose_thresholds_breaks_ties_towards_the_defaults():
    records = [_case("q-1", "answer", "question", confidence=0.95, needs_detail=0.05)]
    chosen = metrics.choose_thresholds(records, confidence_grid=(0.3, 0.5, 0.9), detail_grid=(0.3, 0.6, 0.9))
    assert (chosen["confidence_threshold"], chosen["detail_threshold"]) == (0.5, 0.6)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/live/bench/classify/test_metrics_offline.py -q -n 0`
Expected: FAIL with missing keys and missing `sweep_grid`/`choose_thresholds`.

- [ ] **Step 3: Implement**

In `summarise`, inside the existing loop over `routed`, count the new values next to the existing ones:

```python
    wrong_path = missed_detail = 0
    for depth, record in routed:
        ...  # existing confusion and error counting stays
        if depth != "intake" and depth != record["expected_depth"]:
            wrong_path += 1
        if record.get("ambiguous") and depth != "intake":
            missed_detail += 1
```

Add these to the returned dict, after `"full_as_quick"`:

```python
        "wrong_path": wrong_path,
        "unsafe": answer_as_change + full_as_quick,
        "intake_count": intake_count,
        "missed_detail": missed_detail,
```

Append to `metrics.py`:

```python
DEFAULT_CONFIDENCE = 0.5
DEFAULT_DETAIL = 0.6


def sweep_grid(
    records: list[dict],
    confidence_grid: tuple[float, ...] = DEFAULT_THRESHOLDS,
    detail_grid: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> list[dict]:
    """`summarise` at every (confidence, detail) pair, replayed from stored answers (spec §4.2)."""
    rows = []
    for confidence in confidence_grid:
        for detail in detail_grid:
            summary = summarise(records, confidence_threshold=confidence, detail_threshold=detail)
            rows.append({
                "confidence_threshold": confidence, "detail_threshold": detail,
                **{key: summary[key] for key in ("depth_accuracy", "wrong_path", "unsafe", "intake_count", "missed_detail")},
            })
    return rows


def choose_thresholds(
    records: list[dict],
    confidence_grid: tuple[float, ...] = DEFAULT_THRESHOLDS,
    detail_grid: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> dict:
    """The grid row a backend should run at (plan B, ruling R1): fewest unsafe errors, then wrong
    paths, then vague requests not asked about, then deferrals; ties go to the row nearest today's
    defaults, then the lower thresholds."""

    def key(row: dict) -> tuple:
        distance = abs(row["confidence_threshold"] - DEFAULT_CONFIDENCE) + abs(row["detail_threshold"] - DEFAULT_DETAIL)
        return (
            row["unsafe"], row["wrong_path"], row["missed_detail"], row["intake_count"], round(distance, 6),
            row["confidence_threshold"], row["detail_threshold"],
        )

    return min(sweep_grid(records, confidence_grid, detail_grid), key=key)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/live/bench/classify -q -n 0`
Expected: PASS. The `-m bench` test is skipped without `PHIL_BENCH_CONFIG`.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`. Commit message: `Count wrong paths, deferrals and missed detail separately; sweep both thresholds`, plus the trailer.

---

### Task 3: The revised decision rule and a tune/check report

**Files:**
- Modify: `tests/live/bench/classify/run.py`. Update `decision_rule`, `_report` and the constants, and add `_relabel`.
- Test: `tests/live/bench/classify/test_run_offline.py`

**Interfaces:**
- Consumes: `metrics.summarise` (with the Task 2 keys) and `metrics.choose_thresholds`.
- Produces:
  - `decision_rule(jev: dict, llm: dict) -> dict` with conditions `wrong_path_within_one_of_llm`, `unsafe_no_more_than_llm`, `deferrals_within_two_of_llm`, `p95_latency_at_most_a_third_of_llm` and `cost_per_100_lower_than_llm`;
  - `_relabel(records: list[dict], cases: list[dict]) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

In `test_run_offline.py`, replace `JEV_PASSING`, `LLM_BASELINE` and every `decision_rule` test with:

```python
JEV_PASSING = {"wrong_path": 2, "unsafe": 0, "intake_count": 6, "latency_p95_ms": 50, "cost_per_100": 1.0}
LLM_BASELINE = {"wrong_path": 1, "unsafe": 1, "intake_count": 4, "latency_p95_ms": 300, "cost_per_100": 2.0}


def test_decision_rule_clear_yes():
    result = run.decision_rule(JEV_PASSING, LLM_BASELINE)
    assert result["conditions"] == {
        "wrong_path_within_one_of_llm": True,  # 2 <= 1 + 1
        "unsafe_no_more_than_llm": True,
        "deferrals_within_two_of_llm": True,  # 6 <= 4 + 2
        "p95_latency_at_most_a_third_of_llm": True,
        "cost_per_100_lower_than_llm": True,
    }
    assert result["verdict"] == "yes"


@pytest.mark.parametrize(
    ("change", "condition"),
    [
        ({"wrong_path": 3}, "wrong_path_within_one_of_llm"),
        ({"unsafe": 2}, "unsafe_no_more_than_llm"),
        ({"intake_count": 7}, "deferrals_within_two_of_llm"),
        ({"latency_p95_ms": 101}, "p95_latency_at_most_a_third_of_llm"),
        ({"cost_per_100": 2.0}, "cost_per_100_lower_than_llm"),
    ],
)
def test_decision_rule_each_condition_can_say_no(change, condition):
    result = run.decision_rule({**JEV_PASSING, **change}, LLM_BASELINE)
    assert result["conditions"][condition] is False
    assert [name for name, ok in result["conditions"].items() if ok is not True] == [condition]
    assert result["verdict"] == "no"


def test_decision_rule_unknown_cost_is_undecided_unless_another_condition_fails():
    assert run.decision_rule({**JEV_PASSING, "cost_per_100": None}, LLM_BASELINE)["verdict"] == "undecided (cost unknown)"
    failing = {**JEV_PASSING, "cost_per_100": None, "unsafe": 5}
    assert run.decision_rule(failing, LLM_BASELINE)["verdict"] == "no"


def test_relabel_takes_labels_and_split_from_the_cases_file():
    records = [
        {"case_id": "f-01", "backend": "jev", "expected_class": "feature", "expected_depth": "full", "ambiguous": False},
        {"case_id": "gone", "backend": "jev", "expected_class": "feature", "expected_depth": "full", "ambiguous": False},
    ]
    cases = [{"id": "f-01", "expected_class": "simple_change", "expected_depth": "quick", "ambiguous": False}]
    [relabelled] = run._relabel(records, cases)  # a record whose case is gone is dropped
    assert (relabelled["expected_class"], relabelled["expected_depth"], relabelled["split"]) == ("simple_change", "quick", "tune")
```

Keep the existing tests for `_latest_per_case`, `append_record`, `_error_value` and the backends.

Add one report test. It writes two backends' records to a results file through the same env var the existing `results_path` tests use, then captures `_report`'s output with `capsys`:

```python
def test_report_chooses_on_tune_and_shows_check(tmp_path, monkeypatch, capsys):
    def rec(case_id, backend, task_class, depth, needs_detail=0.1, cost=0.0, latency=10):
        return {
            "case_id": case_id, "backend": backend, "expected_class": task_class, "expected_depth": depth,
            "ambiguous": False, "task_class": task_class, "probabilities": {task_class: 0.9}, "confidence": 0.9,
            "needs_detail": needs_detail, "latency_ms": latency, "input_tokens": 1, "output_tokens": 0,
            "cost_usd": cost, "error": None,
        }

    cases = [
        {"id": "q-01", "expected_class": "question", "expected_depth": "answer", "ambiguous": False},
        {"id": "n-08", "expected_class": "simple_change", "expected_depth": "quick", "ambiguous": False, "split": "check"},
    ]
    monkeypatch.setattr(run, "load_cases", lambda path=None: cases)
    records = [
        rec("q-01", "jev", "question", "answer", latency=10), rec("n-08", "jev", "simple_change", "quick", latency=10),
        rec("q-01", "llm", "question", "answer", cost=0.001, latency=900),
        rec("n-08", "llm", "simple_change", "quick", cost=0.001, latency=900),
    ]
    run._report(records)
    out = capsys.readouterr().out
    assert "chosen on tune: confidence 0.5, detail 0.6" in out
    assert "check (1 case" in out
    assert "jev worth recommending: yes" in out


def test_report_says_when_a_backend_has_no_tune_results(monkeypatch, capsys):
    cases = [{"id": "n-08", "expected_class": "simple_change", "expected_depth": "quick", "ambiguous": False, "split": "check"}]
    monkeypatch.setattr(run, "load_cases", lambda path=None: cases)
    record = {
        "case_id": "n-08", "backend": "jev", "expected_class": "simple_change", "expected_depth": "quick",
        "ambiguous": False, "task_class": "simple_change", "probabilities": {}, "confidence": 0.9,
        "needs_detail": 0.1, "latency_ms": 10, "input_tokens": 1, "output_tokens": 0, "cost_usd": 0.0, "error": None,
    }
    run._report([record])
    assert "--- jev: no tune results ---" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/live/bench/classify/test_run_offline.py -q -n 0`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `run.py`, replace `DEPTH_ACCURACY_SLACK = 0.02` and its comment with:

```python
# Jev is worth recommending over the llm backend when all five conditions hold (spec 2026-10-03
# §4.1, which replaces the M3 spec's §5.1 rule).
WRONG_PATH_SLACK = 1
DEFERRAL_SLACK = 2
```

Keep `LATENCY_FACTOR = 3`.

Replace `decision_rule` with:

```python
def decision_rule(jev: dict, llm: dict) -> dict:
    """The revised rule (spec 2026-10-03 §4.1): is Jev worth recommending over the llm backend?
    All five conditions must hold. Any known condition failing makes it "no"; only when every
    known condition passes does an unknown cost on either side make it undecided."""
    wrong_path_ok = jev["wrong_path"] <= llm["wrong_path"] + WRONG_PATH_SLACK
    unsafe_ok = jev["unsafe"] <= llm["unsafe"]
    deferrals_ok = jev["intake_count"] <= llm["intake_count"] + DEFERRAL_SLACK
    latency_ok = jev["latency_p95_ms"] <= llm["latency_p95_ms"] / LATENCY_FACTOR
    jev_cost, llm_cost = jev["cost_per_100"], llm["cost_per_100"]
    cost_unknown = jev_cost is None or llm_cost is None
    cost_ok: bool | Literal["unknown"] = "unknown" if cost_unknown else jev_cost < llm_cost
    conditions = {
        "wrong_path_within_one_of_llm": wrong_path_ok,
        "unsafe_no_more_than_llm": unsafe_ok,
        "deferrals_within_two_of_llm": deferrals_ok,
        "p95_latency_at_most_a_third_of_llm": latency_ok,
        "cost_per_100_lower_than_llm": cost_ok,
    }
    if not (wrong_path_ok and unsafe_ok and deferrals_ok and latency_ok) or cost_ok is False:
        verdict = "no"  # a known condition failed: an unknown cost can't rescue it
    elif cost_unknown:
        verdict = "undecided (cost unknown)"
    else:
        verdict = "yes"
    return {"conditions": conditions, "verdict": verdict}


def _relabel(records: list[dict], cases: list[dict]) -> list[dict]:
    """`records` with labels and split taken from the current cases file, so a relabelled case is
    rescored without a rerun. A record whose case is no longer in the file is dropped."""
    by_id = {case["id"]: case for case in cases}
    relabelled = []
    for record in records:
        case = by_id.get(record["case_id"])
        if case is None:
            continue
        relabelled.append({
            **record,
            "expected_class": case["expected_class"],
            "expected_depth": case["expected_depth"],
            "ambiguous": case["ambiguous"],
            "split": case.get("split", "tune"),
        })
    return relabelled
```

Replace `_report` with:

```python
def _report(records: list[dict]) -> None:
    latest = _relabel(_latest_per_case(records), load_cases())
    chosen: dict[str, dict] = {}
    for backend in ("jev", "llm"):
        backend_records = [record for record in latest if record.get("backend") == backend]
        tune = [record for record in backend_records if record["split"] == "tune"]
        check = [record for record in backend_records if record["split"] == "check"]
        if not tune:
            print(f"--- {backend}: no tune results ---")
            continue
        row = metrics.choose_thresholds(tune)
        chosen[backend] = {"thresholds": row, "records": backend_records}
        confidence, detail = row["confidence_threshold"], row["detail_threshold"]
        print(f"--- {backend} ---")
        print(f"  chosen on tune: confidence {confidence}, detail {detail}")
        _print_summary("tune", metrics.summarise(tune, confidence_threshold=confidence, detail_threshold=detail))
        if check:
            print(f"  check ({len(check)} case{'s' if len(check) != 1 else ''}):")
            _print_summary("check", metrics.summarise(check, confidence_threshold=confidence, detail_threshold=detail))
    if "jev" in chosen and "llm" in chosen:
        summaries = {
            backend: metrics.summarise(
                entry["records"],
                confidence_threshold=entry["thresholds"]["confidence_threshold"],
                detail_threshold=entry["thresholds"]["detail_threshold"],
            )
            for backend, entry in chosen.items()
        }
        result = decision_rule(summaries["jev"], summaries["llm"])
        print("--- decision rule (spec 2026-10-03 §4.1), all cases at each backend's chosen thresholds ---")
        for name, value in result["conditions"].items():
            print(f"  {name}: {value}")
        print(f"  jev worth recommending: {result['verdict']}")
```

Replace `_print_summary` with a version that takes a label and a summary only:

```python
def _print_summary(label: str, summary: dict) -> None:
    keys = (
        "n", "errors", "depth_accuracy", "class_accuracy", "wrong_path", "unsafe", "intake_count", "missed_detail",
        "answer_as_change", "full_as_quick", "detail_precision", "detail_recall", "latency_p50_ms", "latency_p95_ms",
        "cost_per_100",
    )
    print(f"  [{label}] " + ", ".join(f"{key}={summary[key]}" for key in keys))
    for kind, count in summary.get("errors_by_kind", {}).items():
        print(f"    error {kind}: {count}")
```

Remove the `RoutingConfig` import if it's now unused. Update the module docstring's `--report` sentence: "`--report` replays the stored records with no new calls: it chooses each backend's thresholds on the `tune` cases, shows the `check` cases at those thresholds, and applies the revised decision rule."

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/live/bench/classify -q -n 0`
Expected: PASS.

- [ ] **Step 5: Run the full suite, then commit**

Run: `uv run pytest -q`. Commit message: `Apply the revised Jev decision rule with thresholds chosen on tune cases`, plus the trailer.

---

### Task 4: Cases: relabel f-01, add check cases, revise the spec text

**Files:**
- Modify: `tests/live/bench/classify/cases.jsonl` (line 25 `f-01`, plus 10 appended lines)
- Modify: `tests/live/bench/classify/test_classify_bench.py`
- Modify: `docs/superpowers/specs/2026-09-30-phil-m3-proportional-orchestration-design.md` §5.1

**Interfaces:**
- Consumes: the `split` and `note` fields that the Task 3 report reads (`split` defaults to `tune`).

- [ ] **Step 1: Write the failing tests**

In `test_classify_bench.py`, replace `EXPECTED_COUNTS` and the counts test with:

```python
EXPECTED_TUNE_COUNTS = {
    "question": 6, "diagnosis": 4, "small_operation": 4, "simple_change": 7, "focused_fix": 4,
    "feature": 3, "refactor": 3, "design": 2, "broad_project": 1, "ambiguous": 6,
}


def test_cases_file_has_the_expected_counts_per_kind():
    cases = load_cases()
    tune = [case for case in cases if case.get("split", "tune") == "tune"]
    check = [case for case in cases if case.get("split") == "check"]
    assert len(tune) == 40 and len(check) == 10
    counts = Counter("ambiguous" if case["ambiguous"] else case["expected_class"] for case in tune)
    assert counts == EXPECTED_TUNE_COUNTS
    assert sum(case["ambiguous"] for case in check) == 7


def test_f01_is_relabelled_with_its_reason():
    [f01] = [case for case in load_cases() if case["id"] == "f-01"]
    assert (f01["expected_class"], f01["expected_depth"]) == ("simple_change", "quick")
    assert "py-multiply" in f01["note"]


def test_the_cta_and_pricing_requests_are_check_cases_that_need_detail():
    by_id = {case["id"]: case for case in load_cases()}
    for case_id in ("n-01", "n-02"):
        assert by_id[case_id]["split"] == "check" and by_id[case_id]["ambiguous"] is True
        assert by_id[case_id]["expected_depth"] == "full"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/live/bench/classify/test_classify_bench.py -q -n 0`
Expected: FAIL.

- [ ] **Step 3: Edit the cases**

Replace line 25 of `cases.jsonl` (`f-01`) with:

```json
{"id": "f-01", "request": "Add a multiply function to calc with tests, matching how add and subtract are done.", "chat": [], "repo": {"test_cmd": "pytest", "files": ["calc.py", "tests/test_calc.py", "pyproject.toml"], "file_count": 3}, "expected_class": "simple_change", "expected_depth": "quick", "ambiguous": false, "note": "Relabelled 2026-10-06 from feature/full: one function in one file, following an existing pattern; the M3b end-to-end benchmark expects its twin py-multiply to take the quick path."}
```

Append these 10 lines. They all use the same Next.js landing-page repo:

```json
{"id": "n-01", "split": "check", "request": "Add a CTA section at the bottom of the page featuring the AI systems audit and the AI readiness guide.", "chat": [], "repo": {"test_cmd": "npm test", "files": ["package.json", "src/app/page.tsx", "src/app/layout.tsx", "src/app/globals.css", "src/components/Header.tsx", "src/components/Footer.tsx", "src/components/LeadCaptureForm.tsx", "src/components/ResultsSummary.tsx"], "file_count": 8}, "expected_class": "feature", "expected_depth": "full", "ambiguous": true}
{"id": "n-02", "split": "check", "request": "Let's add a pricing section with animations.", "chat": [], "repo": {"test_cmd": "npm test", "files": ["package.json", "src/app/page.tsx", "src/app/layout.tsx", "src/app/globals.css", "src/components/Header.tsx", "src/components/Footer.tsx", "src/components/LeadCaptureForm.tsx", "src/components/ResultsSummary.tsx"], "file_count": 8}, "expected_class": "feature", "expected_depth": "full", "ambiguous": true}
{"id": "n-03", "split": "check", "request": "Make the homepage look better.", "chat": [], "repo": {"test_cmd": "npm test", "files": ["package.json", "src/app/page.tsx", "src/app/layout.tsx", "src/app/globals.css", "src/components/Header.tsx", "src/components/Footer.tsx", "src/components/LeadCaptureForm.tsx", "src/components/ResultsSummary.tsx"], "file_count": 8}, "expected_class": "design", "expected_depth": "full", "ambiguous": true}
{"id": "n-04", "split": "check", "request": "Update the footer links.", "chat": [], "repo": {"test_cmd": "npm test", "files": ["package.json", "src/app/page.tsx", "src/app/layout.tsx", "src/app/globals.css", "src/components/Header.tsx", "src/components/Footer.tsx", "src/components/LeadCaptureForm.tsx", "src/components/ResultsSummary.tsx"], "file_count": 8}, "expected_class": "simple_change", "expected_depth": "quick", "ambiguous": true}
{"id": "n-05", "split": "check", "request": "Add our new logo to the header.", "chat": [], "repo": {"test_cmd": "npm test", "files": ["package.json", "src/app/page.tsx", "src/app/layout.tsx", "src/app/globals.css", "src/components/Header.tsx", "src/components/Footer.tsx", "src/components/LeadCaptureForm.tsx", "src/components/ResultsSummary.tsx"], "file_count": 8}, "expected_class": "simple_change", "expected_depth": "quick", "ambiguous": true}
{"id": "n-06", "split": "check", "request": "Change the hero headline to something punchier.", "chat": [], "repo": {"test_cmd": "npm test", "files": ["package.json", "src/app/page.tsx", "src/app/layout.tsx", "src/app/globals.css", "src/components/Header.tsx", "src/components/Footer.tsx", "src/components/LeadCaptureForm.tsx", "src/components/ResultsSummary.tsx"], "file_count": 8}, "expected_class": "simple_change", "expected_depth": "quick", "ambiguous": true}
{"id": "n-07", "split": "check", "request": "Add a testimonials section.", "chat": [], "repo": {"test_cmd": "npm test", "files": ["package.json", "src/app/page.tsx", "src/app/layout.tsx", "src/app/globals.css", "src/components/Header.tsx", "src/components/Footer.tsx", "src/components/LeadCaptureForm.tsx", "src/components/ResultsSummary.tsx"], "file_count": 8}, "expected_class": "feature", "expected_depth": "full", "ambiguous": true}
{"id": "n-08", "split": "check", "request": "Rename the 'Get started' button in the header to 'Start free trial'.", "chat": [], "repo": {"test_cmd": "npm test", "files": ["package.json", "src/app/page.tsx", "src/app/layout.tsx", "src/app/globals.css", "src/components/Header.tsx", "src/components/Footer.tsx", "src/components/LeadCaptureForm.tsx", "src/components/ResultsSummary.tsx"], "file_count": 8}, "expected_class": "simple_change", "expected_depth": "quick", "ambiguous": false}
{"id": "n-09", "split": "check", "request": "Why does the lead capture form submit twice when I press Enter?", "chat": [], "repo": {"test_cmd": "npm test", "files": ["package.json", "src/app/page.tsx", "src/app/layout.tsx", "src/app/globals.css", "src/components/Header.tsx", "src/components/Footer.tsx", "src/components/LeadCaptureForm.tsx", "src/components/ResultsSummary.tsx"], "file_count": 8}, "expected_class": "diagnosis", "expected_depth": "answer", "ambiguous": false}
{"id": "n-10", "split": "check", "request": "Add a dark mode toggle to the header that remembers the choice in localStorage and applies it before the page paints.", "chat": [], "repo": {"test_cmd": "npm test", "files": ["package.json", "src/app/page.tsx", "src/app/layout.tsx", "src/app/globals.css", "src/components/Header.tsx", "src/components/Footer.tsx", "src/components/LeadCaptureForm.tsx", "src/components/ResultsSummary.tsx"], "file_count": 8}, "expected_class": "feature", "expected_depth": "full", "ambiguous": false}
```

Check that `test_every_case_has_a_known_class_and_depth` and `test_case_ids_are_unique` still pass. Check that `load_cases`, and `_state(case)` in `run.py`, ignore the extra `split` and `note` keys. If `_state` passes the whole case into `RouteState`, which forbids extra fields, build it from the known keys only.

- [ ] **Step 4: Revise the M3 spec's rule**

In `docs/superpowers/specs/2026-09-30-phil-m3-proportional-orchestration-design.md` §5.1:
- Replace the "**Decision rule.**" paragraph and its three bullets with: "**Decision rule:** replaced on 2026-10-06 by the revised rule in `2026-10-03-phil-ask-before-acting-design.md` §4.1: wrong paths and deferrals are counted separately, each with a tolerance; thresholds are per classifier and chosen on the `tune` cases, then checked on the `check` cases. The original rule and its 'no' verdict are in journal part 3."
- In the **Report** list, add "a two-dimensional sweep of the confidence and detail thresholds".

- [ ] **Step 5: Run the tests to verify they pass, then the full suite, then commit**

Run: `uv run pytest tests/live/bench/classify -q -n 0`, then `uv run pytest -q`.
Commit message: `Relabel f-01, add ten check cases with the CTA and pricing requests, point §5.1 at the revised rule`, plus the trailer.

---

## After the plan: the live run (the user)

The user runs this; it isn't part of any task:

```
PHIL_BENCH_CONFIG=~/phil-bench.toml uv run pytest -m bench tests/live/bench/classify -n 0
uv run python -m tests.live.bench.classify.run --report
```

The report shows:
- each backend's chosen thresholds;
- its `tune` and `check` results;
- the revised decision rule's verdict.

Defaults change only after we've read the check results together. If they hold up:
- set `routing.jev_detail_threshold`, and `routing.confidence_threshold` if the chosen value differs;
- write the outcome in journal part 5.
