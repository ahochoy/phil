"""A failure in plain words (spec 2026-10-07 callouts §3.5): what happened, whether Phil retries,
and what the user can do. Built on retry.py's status and transient checks; never raises."""

import re
from dataclasses import asdict, dataclass

CATEGORIES = ("auth", "quota", "busy", "network", "refused", "output", "config", "internal")
_QUOTA_WORDS = re.compile(r"credit|quota|insufficient|billing|payment", re.IGNORECASE)
_KEY_ENV = {"openrouter": "OPENROUTER_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY", "typesafe": "TYPESAFE_API_KEY"}


@dataclass(frozen=True)
class Failure:
    category: str
    headline: str
    retries: str
    action: str

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Failure":
        return cls(**{k: str(data.get(k, "")) for k in ("category", "headline", "retries", "action")})


def _status(exc: BaseException) -> int | None:
    for getter in (lambda e: e.status_code, lambda e: e.response.status_code):
        try:
            value = getter(exc)
        except Exception:
            continue
        if isinstance(value, int):
            return value
    return None


def classify_failure(exc: BaseException, *, provider: str | None = None, attempts: int | None = None) -> Failure:
    try:
        return _classify(exc, provider or "the provider", attempts)
    except Exception:
        return Failure("internal", f"Something went wrong inside Phil ({type(exc).__name__}).",
                       "Phil won't retry this.", "Details: /more 1")


def _classify(exc: BaseException, provider: str, attempts: int | None) -> Failure:
    from phil.agents.invoke import ContractViolation
    from phil.agents.retry import is_transient, provider_detail
    from phil.config import ConfigError

    if isinstance(exc, ContractViolation):
        return Failure("output", f"The {exc.agent} didn't return a usable answer.", "Phil already retried.",
                       f"Try again, or use a stronger model for {exc.agent}.")
    if isinstance(exc, ConfigError):
        return Failure("config", str(exc), "Phil won't retry this.", "Fix the setting it names.")
    status = _status(exc)
    detail = provider_detail(exc) or str(exc)
    if status in (401, 403):
        env = _KEY_ENV.get(provider, f"{provider.upper()}_API_KEY")
        return Failure("auth", "The provider rejected your API key.", "Phil won't retry this.",
                       f"Run `phil keys set {provider}`, or set {env}.")
    if status == 402 or (status is not None and 400 <= status < 500 and _QUOTA_WORDS.search(detail)):
        return Failure("quota", f"Your {provider} account is out of credits.", "Phil won't retry this.",
                       "Add credits, then try again.")
    if status in (429, 529):
        n = f" {attempts} times" if attempts else ""
        return Failure("busy", f"{provider} is busy. Phil retried{n}.", "Phil already retried.",
                       "Try again in a minute.")
    if is_transient(exc):
        return Failure("network", f"Couldn't reach {provider}. Phil retried.", "Phil already retried.",
                       "Check your connection, then try again.")
    if status is not None and 400 <= status < 500:
        return Failure("refused", f"{provider} refused the request: {detail.splitlines()[0][:160]}",
                       "Phil won't retry this.", "Check the model with `phil models check`.")
    return Failure("internal", f"Something went wrong inside Phil ({type(exc).__name__}).",
                   "Phil won't retry this.", "Details: /more 1")
