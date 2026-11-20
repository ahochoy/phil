"""Summarising an agent's last messages, and recovering an almost-right answer from them."""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from phil.agents.salvage import LAST_MESSAGES, Salvaged, extract_json, salvage, summarise


def _accepts_empty_plan(data: dict) -> list[str]:
    return [] if data == {"summary": "s", "tasks": []} else ["tasks: Field required"]


def test_summarise_keeps_the_last_six_oldest_first_with_calls_and_finish_reason():
    messages = [HumanMessage(content=f"m{i}") for i in range(4)]
    messages += [
        AIMessage(
            content="calling",
            tool_calls=[{"name": "read_file", "args": {"file_path": "a.py"}, "id": "c1"}],
            response_metadata={"finish_reason": "tool_calls"},
        ),
        ToolMessage(content="file body", tool_call_id="c1"),
        AIMessage(
            content="",
            invalid_tool_calls=[{"name": "Plan", "args": '{"summary": ', "id": "c2", "error": "bad json"}],
            response_metadata={"stop_reason": "max_tokens"},
        ),
        HumanMessage(content="last"),
    ]
    rows = summarise(messages)
    assert LAST_MESSAGES == 6
    assert [row["role"] for row in rows] == ["human", "human", "ai", "tool", "ai", "human"]
    assert [row["text"] for row in rows] == ["m2", "m3", "calling", "file body", "", "last"]
    assert rows[2]["tool_calls"] == [{"name": "read_file", "args": json.dumps({"file_path": "a.py"})}]
    assert rows[2]["finish_reason"] == "tool_calls"
    assert rows[4]["invalid_tool_calls"] == [{"name": "Plan", "args": '{"summary": ', "error": "bad json"}]
    assert rows[4]["finish_reason"] == "max_tokens"
    assert "tool_calls" not in rows[0]


def test_summarise_cuts_long_text_and_args():
    rows = summarise(
        [
            AIMessage(
                content="x" * 50,
                tool_calls=[{"name": "t", "args": {"k": "y" * 50}, "id": "c"}],
                invalid_tool_calls=[{"name": "t", "args": "z" * 50, "id": "d", "error": "e"}],
            )
        ],
        limit=10,
    )
    assert rows[0]["text"] == "x" * 10
    assert len(rows[0]["tool_calls"][0]["args"]) == 10
    assert rows[0]["invalid_tool_calls"][0]["args"] == "z" * 10


def test_summarise_writes_images_as_a_placeholder():
    huge = "A" * 5000
    message = HumanMessage(
        content=[
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{huge}"}},
            {"type": "image", "source_type": "base64", "data": huge, "mime_type": "image/png"},
        ]
    )
    (row,) = summarise([message])
    assert row["text"] == "look[image][image]"
    assert huge not in json.dumps(row)


def test_extract_json_from_a_fence_and_from_braces_with_braces_in_strings():
    assert extract_json('Sure:\n```json\n{"a": 1}\n```\nbye') == '{"a": 1}'
    assert extract_json('x\n```\n{"a": 2}\n```') == '{"a": 2}'
    assert extract_json('Here: {"a": "x}y", "b": {"c": 1}} done') == '{"a": "x}y", "b": {"c": 1}}'
    assert extract_json('{"a": "q\\"}", "b": 1} tail') == '{"a": "q\\"}", "b": 1}'
    assert extract_json("no json here") is None
    assert extract_json("") is None


def test_salvage_repairs_a_truncated_invalid_call():
    message = AIMessage(
        content="",
        invalid_tool_calls=[{"name": "Plan", "args": '{"summary": "s", "tasks": [', "id": "c", "error": "bad"}],
    )
    salvaged, problems = salvage([message], "Plan", _accepts_empty_plan)
    assert salvaged == Salvaged(data={"summary": "s", "tasks": []}, route="invalid_call")
    assert problems == []


def test_salvage_revalidates_an_answer_call():
    message = AIMessage(content="", tool_calls=[{"name": "Plan", "args": {"summary": "s", "tasks": []}, "id": "c"}])
    salvaged, _ = salvage([message], "Plan", _accepts_empty_plan)
    assert salvaged == Salvaged(data={"summary": "s", "tasks": []}, route="answer_call")


def test_salvage_ignores_calls_to_other_tools():
    message = AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"summary": "s", "tasks": []}, "id": "c"}])
    assert salvage([message], "Plan", _accepts_empty_plan) == (None, [])


def test_salvage_reads_json_from_text():
    message = AIMessage(content='Done.\n```json\n{"summary": "s", "tasks": []}\n```')
    salvaged, _ = salvage([message], "Plan", _accepts_empty_plan)
    assert salvaged == Salvaged(data={"summary": "s", "tasks": []}, route="text")


def test_salvage_reads_json_from_text_parts_and_repairs_it():
    message = AIMessage(content=[{"type": "text", "text": 'Answer: {"summary": "s", "tasks": ['}])
    salvaged, _ = salvage([message], "Plan", _accepts_empty_plan)
    assert salvaged is not None and salvaged.route == "text"


def test_salvage_walks_newest_first_and_stops_after_six_ai_messages():
    good = AIMessage(content='{"summary": "s", "tasks": []}')
    filler = [AIMessage(content="thinking") for _ in range(LAST_MESSAGES)]
    assert salvage([good, *filler], "Plan", _accepts_empty_plan) == (None, [])
    assert salvage([good, *filler[:-1]], "Plan", _accepts_empty_plan)[0] is not None


def test_salvage_reports_the_last_problems_when_nothing_validates():
    message = AIMessage(content="", tool_calls=[{"name": "Plan", "args": {"summary": "s"}, "id": "c"}])
    salvaged, problems = salvage([message], "Plan", _accepts_empty_plan)
    assert salvaged is None
    assert problems == ["salvage: tasks: Field required"]


def test_salvage_only_the_newest_attempt_counts():
    older = AIMessage(content='{"summary": "s", "tasks": []}')
    newest = AIMessage(content="", tool_calls=[{"name": "Plan", "args": {"summary": "broken"}, "id": "c"}])
    salvaged, problems = salvage([older, newest], "Plan", _accepts_empty_plan)
    assert salvaged is None
    assert problems == ["salvage: tasks: Field required"]


def test_salvage_skips_newer_messages_without_candidates_to_reach_the_newest_attempt():
    older = AIMessage(content='{"summary": "s", "tasks": []}')
    salvaged, _ = salvage([older, AIMessage(content="thinking")], "Plan", _accepts_empty_plan)
    assert salvaged is not None


def test_salvage_rejects_an_empty_answer_call():
    message = AIMessage(content="", tool_calls=[{"name": "Plan", "args": {}, "id": "c"}])
    salvaged, problems = salvage([message], "Plan", lambda data: [])
    assert salvaged is None
    assert problems and problems[0].startswith("salvage: ")


def test_salvage_rejects_an_empty_text_object_for_a_contract_with_only_defaults():
    message = AIMessage(content="Nothing to record: {}")
    salvaged, problems = salvage([message], "Memory", lambda data: [], fields=frozenset({"notes"}))
    assert salvaged is None
    assert problems and problems[0].startswith("salvage: ")


def test_salvage_rejects_a_text_object_that_sets_none_of_the_contracts_fields():
    message = AIMessage(content='{"unrelated": 1}')
    salvaged, _ = salvage([message], "Memory", lambda data: [], fields=frozenset({"notes"}))
    assert salvaged is None
    accepted, _ = salvage([AIMessage(content='{"notes": []}')], "Memory", lambda data: [], fields=frozenset({"notes"}))
    assert accepted == Salvaged(data={"notes": []}, route="text")


def test_salvage_ignores_a_non_json_fence_before_the_json_fence():
    message = AIMessage(content='Run:\n```bash\nls\n```\nAnswer:\n```json\n{"summary": "s", "tasks": []}\n```')
    salvaged, _ = salvage([message], "Plan", _accepts_empty_plan)
    assert salvaged == Salvaged(data={"summary": "s", "tasks": []}, route="text")


def test_salvage_tries_every_fence_then_every_brace_span():
    message = AIMessage(content='```json\n{"summary": "x"}\n```\n```json\n{"summary": "s", "tasks": []}\n```')
    assert salvage([message], "Plan", _accepts_empty_plan)[0] is not None
    spans = AIMessage(content='First {"summary": "x"} then {"summary": "s", "tasks": []} end')
    assert salvage([spans], "Plan", _accepts_empty_plan)[0] is not None


def test_summarise_caps_tool_calls_per_message_and_counts_the_rest():
    message = AIMessage(
        content="",
        tool_calls=[{"name": "t", "args": {"i": i}, "id": f"c{i}"} for i in range(40)],
        invalid_tool_calls=[{"name": "t", "args": "x", "id": f"d{i}", "error": "e"} for i in range(12)],
    )
    (row,) = summarise([message])
    assert len(row["tool_calls"]) == 10
    assert len(row["invalid_tool_calls"]) == 10
    assert row["tool_calls_omitted"] == 30 + 2
    quiet = summarise([AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c"}])])[0]
    assert "tool_calls_omitted" not in quiet


def test_salvage_returns_nothing_without_candidates():
    messages = [HumanMessage(content="hi"), AIMessage(content="just prose"), ToolMessage(content="x", tool_call_id="c")]
    assert salvage(messages, "Plan", _accepts_empty_plan) == (None, [])
    assert salvage([], "Plan", _accepts_empty_plan) == (None, [])
