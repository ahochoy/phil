import threading

from phil.chat.events import ChatEvent
from phil.chat.state import ChatState, RunView


def test_state_updates_and_view():
    state = ChatState()
    assert state.view().stage == "idle"
    state.set_stage("planning")
    state.set_step("architect", now=10.0)
    state.set_run(RunView("r-1", "CALC", "implement", 1, 2, started=5.0))
    state.set_paused(True)
    state.add_btw(1)
    view = state.view()
    assert (view.stage, view.step, view.step_started) == ("planning", "architect", 10.0)
    assert view.run.run_id == "r-1" and view.paused and view.btw_pending == 1
    state.set_step(None, now=11.0)
    state.add_btw(-1)
    assert state.view().step is None and state.view().btw_pending == 0


def test_state_is_thread_safe():
    state = ChatState()
    threads = [threading.Thread(target=lambda: [state.add_btw(1) for _ in range(1000)]) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state.view().btw_pending == 4000


def test_events_default_to_never_stale():
    assert ChatEvent("btw_answer").generation == -1
