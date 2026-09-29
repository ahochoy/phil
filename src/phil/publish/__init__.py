from phil.publish.publisher import FakePublisher, GhPublisher, PublishError, Publisher, PullRequest

# `make_publisher` is deliberately NOT re-exported here. Tests patch
# `phil.publish.publisher.make_publisher`, and a name imported at module load time (e.g. via
# `from phil.publish import make_publisher`) would bind the unpatched function, letting a test
# reach the real `gh` CLI. Callers must go through the module: `from phil.publish import
# publisher as publishing` then `publishing.make_publisher(repo_root)`.

__all__ = ["FakePublisher", "GhPublisher", "PublishError", "Publisher", "PullRequest"]
