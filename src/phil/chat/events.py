from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChatEvent:
    """Something a background job or the run watcher reports to the chat's main thread."""

    kind: str
    data: dict = field(default_factory=dict)
    # Goal jobs carry the goal generation they belong to; results from a replaced goal are dropped.
    # -1 means the event is never stale (run events, /btw answers).
    generation: int = -1
