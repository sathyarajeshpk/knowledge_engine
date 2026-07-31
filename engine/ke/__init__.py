"""Knowledge Engine.

An AI-vendor-independent engine that builds and maintains structured knowledge
repositories ("Domain Packs") as Markdown in a Git repository.

Design rules that hold across the whole package:

* The scheduled pipeline is a pure function. It never calls an AI model, and
  the same inputs always produce the same outputs.
* Nothing is ever deleted. Knowledge is corrected by appending a revision;
  replaced objects are marked, not removed.
* The engine never writes user-owned fields. See `ke.models` for the machine
  checkable ownership registry that enforces this.
"""

__version__ = "0.1.0"

# The metadata.yaml layout this build of the engine reads and writes.
# `ke migrate` (M9) upgrades objects written by older versions.
SCHEMA_VERSION = 1
