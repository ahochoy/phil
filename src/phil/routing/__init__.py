"""Routing: request classification and routing policy."""

from phil.routing.classes import CLASSES, ClassInfo, DEPTH, DEPTHS, NEEDS_DETAIL_CRITERIA, NEEDS_DETAIL_QUESTION, TASK_CLASS_QUESTION
from phil.routing.policy import OVERRIDES, Route, decide, parse_override, skips_design
from phil.routing.types import Judgement, RouteState, Usage, route_state

__all__ = [
    "CLASSES",
    "DEPTH",
    "DEPTHS",
    "ClassInfo",
    "TASK_CLASS_QUESTION",
    "NEEDS_DETAIL_QUESTION",
    "NEEDS_DETAIL_CRITERIA",
    "Usage",
    "Judgement",
    "RouteState",
    "route_state",
    "Route",
    "parse_override",
    "decide",
    "OVERRIDES",
    "skips_design",
]
