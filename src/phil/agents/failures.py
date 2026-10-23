"""A failure in plain words (spec 2026-10-07 callouts §3.5): what happened, whether Phil retries,
and what the user can do. Built on retry.py's status and transient checks; never raises."""

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phil.agents.providers import ProviderSpec

CATEGORIES = ("auth", "quota", "busy", "server", "network", "refused", "output", "config", "internal")
PROVIDER_ATTR = "phil_provider"  # the ProviderSpec invoke_agent tags a failed call's exception with
_BAD_KEY_PHRASES = ("api_key_invalid", "api key not valid")

# langchain_core's provider-neutral model-error names (langchain_core.exceptions), matched by
# name along the exception's MRO rather than imported, the same way retry.py matches them. Used
# only when `_status` found no HTTP status at all — e.g. langchain_google_genai raises a bare
# `GoogleAuthenticationError(msg)` / `GoogleRateLimitError(msg)` with no status attribute, but
# each subclasses the matching langchain_core type.
_AUTH_NAMES = {"ModelAuthenticationError", "ModelPermissionDeniedError"}
_BUSY_NAMES = {"ModelRateLimitError"}
_REFUSED_NAMES = {"ModelNotFoundError", "ModelInvalidRequestError", "InvalidRequestError"}


def tag_provider(exc: BaseException, spec: "ProviderSpec") -> None:
    """Remember which provider `exc` came from, so its failure names the right key and URL."""
    try:
        setattr(exc, PROVIDER_ATTR, spec)
    except Exception:
        pass


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
        spec = getattr(exc, PROVIDER_ATTR, None)
        name = provider or (spec.name if spec is not None else None)
        return _classify(exc, name, attempts, spec)
    except Exception:
        return internal_failure(type(exc).__name__)


def internal_failure(name: str) -> Failure:
    """Something failed inside Phil; `name` is the exception's type (empty: not named). The
    callout's own Details row points at the raw error, so the action doesn't repeat it."""
    named = f" ({name})" if name else ""
    return Failure("internal", f"Something went wrong inside Phil{named}.", "Phil won't retry this.", "Try again.")


def _subject(provider: str | None) -> str:
    """The provider as a sentence's subject: its name, or "The provider" when it isn't known."""
    return provider or "The provider"


def _object(provider: str | None) -> str:
    """The provider inside a sentence: its name, or "the provider" when it isn't known."""
    return provider or "the provider"


def _key_action(provider: str | None, spec: "ProviderSpec | None") -> str:
    if provider is None:
        return "Check your API keys with `phil keys list`."
    if spec is None:
        from phil.agents.providers import ALIASES, BUILTIN_PROVIDERS

        spec = BUILTIN_PROVIDERS.get(ALIASES.get(provider, provider))
        if spec is None:
            return "Check your API keys with `phil keys list`."
    if spec.api_key_env is None:
        return f"Check [providers.{provider}] in your config: it sends no API key."
    return f"Run `phil keys set {provider}`, or set {spec.api_key_env}."


def _auth_failure(provider: str | None, spec: "ProviderSpec | None" = None) -> Failure:
    return Failure("auth", "The provider rejected your API key.", "Phil won't retry this.", _key_action(provider, spec))


def _busy_failure(provider: str | None, attempts: int | None) -> Failure:
    n = f" {attempts} times" if attempts else ""
    return Failure("busy", f"{_subject(provider)} is busy. Phil retried{n}.", "Phil already retried.",
                   "Try again in a minute.")


def _refused_failure(provider: str | None, detail: str) -> Failure:
    first = (detail.splitlines() or [""])[0][:160]
    return Failure("refused", f"{_subject(provider)} refused the request: {first}",
                   "Phil won't retry this.", "Check the model with `phil models check`.")


_CONTENT = {"image": "an image", "file": "a file", "audio": "an audio clip", "video": "a video"}
_ROLE = {"tool": "a tool result", "user": "your message", "assistant": "an agent's reply", "system": "the instructions"}


def _unsendable_content(exc: BaseException, provider: str | None) -> Failure | None:
    """The OpenRouter SDK checks a request before sending it and raises a pydantic ValidationError
    (titled `Unmarshaller`) when a message holds content not in its form, such as a file block an
    agent's tool returned. That's a refusal of what was sent, not a bug inside Phil: say what it
    was and where. None for any other exception."""
    from pydantic import ValidationError

    if not isinstance(exc, ValidationError) or exc.title != "Unmarshaller":
        return None
    what, where = "content", "a message"
    for error in exc.errors():
        location = error.get("loc", ())
        if len(location) > 2 and location[0] == "body" and isinstance(location[2], str):
            where = _ROLE.get(location[2], where)
        tag = (error.get("ctx") or {}).get("tag")
        if isinstance(tag, str):
            what = _CONTENT.get(tag, f"a {tag} part")
            break
    return Failure("refused", f"Phil couldn't send the request: {_object(provider)} doesn't accept {what} in {where}.",
                   "Phil won't retry this.", "Try again without that file, or try a different model.")


def _classify(
    exc: BaseException, provider: str | None, attempts: int | None, spec: "ProviderSpec | None"
) -> Failure:
    from phil.agents.invoke import ContractViolation
    from phil.agents.retry import is_transient, local_server_down, looks_like_quota, provider_detail
    from phil.config import ConfigError

    if isinstance(exc, ContractViolation):
        return Failure("output", f"The {exc.agent} didn't return a usable answer.", "Phil already retried.",
                       f"Try again, or use a stronger model for {exc.agent}.")
    if isinstance(exc, ConfigError):
        return Failure("config", str(exc), "Phil won't retry this.", "Fix the setting it names.")
    if (unsendable := _unsendable_content(exc, provider)) is not None:
        return unsendable
    status = _status(exc)
    detail = provider_detail(exc) or str(exc)
    if any(phrase in detail.lower() for phrase in _BAD_KEY_PHRASES):  # Google's bad key is a 400
        return _auth_failure(provider, spec)
    client_error = status is None or 400 <= status < 500
    if status == 402 or (client_error and looks_like_quota(detail)):  # before "busy": a 429 can be quota
        return Failure("quota", f"Your {provider or 'provider'} account is out of credits.", "Phil won't retry this.",
                       "Add credits, then try again.")
    if (local := local_server_down(exc)) is not None:
        base = spec.base_url if spec is not None and spec.base_url else local
        action = "Is `ollama serve` running?" if provider == "ollama" else f"Is the server at {base} running?"
        return Failure("network", f"Couldn't reach {_object(provider)} at {base}.", "Phil won't retry this.", action)
    if status is None:
        names = _mro_names(exc)
        if names & _AUTH_NAMES:
            return _auth_failure(provider, spec)
        if names & _BUSY_NAMES:
            return _busy_failure(provider, attempts)
        if names & _REFUSED_NAMES:
            return _refused_failure(provider, detail)
    if status in (401, 403):
        return _auth_failure(provider, spec)
    if status in (429, 529):
        return _busy_failure(provider, attempts)
    if status is not None and 500 <= status < 600:
        return Failure("server", f"{_subject(provider)} had a server error. Phil retried.", "Phil already retried.",
                       "Try again in a few minutes.")
    if is_transient(exc):
        return Failure("network", f"Couldn't reach {_object(provider)}. Phil retried.", "Phil already retried.",
                       "Check your connection, then try again.")
    if status is not None and 400 <= status < 500:
        return _refused_failure(provider, detail)
    return internal_failure(type(exc).__name__)
