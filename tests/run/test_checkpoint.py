from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from phil.run.checkpoint import open_checkpointer


class CounterState(TypedDict):
    n: int
    answers: list[str]


def build(db_path):
    def work(state: CounterState) -> dict:
        return {"n": state["n"] + 1}

    def ask(state: CounterState) -> dict:
        return {"answers": [*state["answers"], interrupt({"n": state["n"]})]}

    graph = StateGraph(CounterState)
    graph.add_node("work", work)
    graph.add_node("ask", ask)
    graph.add_edge(START, "work")
    graph.add_edge("work", "ask")
    graph.add_edge("ask", END)
    return graph.compile(checkpointer=open_checkpointer(db_path))


def test_interrupted_run_resumes_from_a_fresh_checkpointer(tmp_path):
    db_path = tmp_path / "nested" / "phil.db"
    config = {"configurable": {"thread_id": "r-0001"}}
    first = build(db_path)
    result = first.invoke({"n": 0, "answers": []}, config)
    assert result["__interrupt__"][0].value == {"n": 1}

    second = build(db_path)
    assert second.get_state(config).next == ("ask",)
    final = second.invoke(Command(resume="yes"), config)
    assert final == {"n": 1, "answers": ["yes"]}
