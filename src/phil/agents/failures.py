"""A failure in plain words (spec 2026-10-07 callouts §3.5): what happened, whether Phil retries,
and what the user can do. Built on retry.py's status and transient checks; never raises."""

from dataclasses import asdict, dataclass

CATEGORIES = ("auth", "quota", "busy", "server", "network", "refused", "output", "config", "internal")
_KEY_ENV = {"openrouter": "OPENROUTER_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY", "typesafe": "TYPESAFE_API_KEY"}

# Specific phrases, not single keywords: a bare "credit", "quota", "billing" or "payment" also
# shows up in refusals that have nothing to do with the account being out of money (e.g. "model
# billing-assistant not found", "payment field invalid"), so those would false-positive as quota.
_QUOTA_PHRASES = (
    "insufficient credits",
    "insufficient_quota",
    "insufficient funds",
    "out of credits",
    "exceeded your current quota",
    "credit balance",
    "requires more credits",
)

# langchain_core's provider-neutral model-error names (langchain_core.exceptions), matched by
# name along the exception's MRO rather than imported, the same way retry.py matches them. Used
# only when `_status` found no HTTP status at all — e.g. langchain_google_genai raises a bare
# `GoogleAuthenticationError(msg)` / `GoogleRateLimitError(msg)` with no status attribute, but
# each subclasses the matching langchain_core type.
_AUTH_NAMES = {"ModelAuthenticationError", "ModelPermissionDeniedError"}
_BUSY_NAMES = {"ModelRateLimitError"}
_REFUSED_NAMES = {"ModelNotFoundError", "ModelInvalidRequestError", "InvalidRequestError"}


def _looks_like_quota(detail: str) -> bool:
    lowered = detail.lower()
    return any(phrase in lowered for phrase in _QUOTA_PHRASES)


def _mro_names(exc: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


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


def _auth_failure(provider: str) -> Failure:
    env = _KEY_ENV.get(provider, f"{provider.upper()}_API_KEY")
    return Failure("auth", "The provider rejected your API key.", "Phil won't retry this.",
                   f"Run `phil keys set {provider}`, or set {env}.")


def _busy_failure(provider: str, attempts: int | None) -> Failure:
    n = f" {attempts} times" if attempts else ""
    return Failure("busy", f"{provider} is busy. Phil retried{n}.", "Phil already retried.",
                   "Try again in a minute.")


def _refused_failure(provider: str, detail: str) -> Failure:
    return Failure("refused", f"{provider} refused the request: {detail.splitlines()[0][:160]}",
                   "Phil won't retry this.", "Check the model with `phil models check`.")


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
    if status is None:
        names = _mro_names(exc)
        if names & _AUTH_NAMES:
            return _auth_failure(provider)
        if names & _BUSY_NAMES:
            return _busy_failure(provider, attempts)
        if names & _REFUSED_NAMES:
            return _refused_failure(provider, detail)
    if status in (401, 403):
        return _auth_failure(provider)
    if status == 402 or (status is not None and 400 <= status < 500 and _looks_like_quota(detail)):
        return Failure("quota", f"Your {provider} account is out of credits.", "Phil won't retry this.",
                       "Add credits, then try again.")
    if status in (429, 529):
        return _busy_failure(provider, attempts)
    if status is not None and 500 <= status < 600:
        return Failure("server", f"{provider} had a server error. Phil retried.", "Phil already retried.",
                       "Try again in a few minutes.")
    if is_transient(exc):
        return Failure("network", f"Couldn't reach {provider}. Phil retried.", "Phil already retried.",
                       "Check your connection, then try again.")
    if status is not None and 400 <= status < 500:
        return _refused_failure(provider, detail)
    return Failure("internal", f"Something went wrong inside Phil ({type(exc).__name__}).",
                   "Phil won't retry this.", "Details: /more 1")
