import logging

from phil.publish.publisher import FakePublisher, GhPublisher, PublishError, Publisher, PullRequest

# `make_publisher` is deliberately NOT re-exported here. Tests patch
# `phil.publish.publisher.make_publisher`, and a name imported at module load time (e.g. via
# `from phil.publish import make_publisher`) would bind the unpatched function, letting a test
# reach the real `gh` CLI. Callers must go through the module: `from phil.publish import
# publisher as publishing` then `publishing.make_publisher(repo_root)`.

__all__ = ["FakePublisher", "GhPublisher", "PublishError", "Publisher", "PullRequest"]

# Sweeps log failures under `phil.publish` for developers; they must never be printed. Without a
# handler in the hierarchy (plain CLI commands configure none), Python's last-resort handler would
# write them to stderr. Records still propagate to `phil` handlers (the chat's phil.log).
logging.getLogger(__name__).addHandler(logging.NullHandler())
