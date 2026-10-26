"""Shared literal facts of the tool's outputs.

Extracted during the scattered-constants dogfood (the py port of upstream's
constants.dart): one business fact, one spelling — the empty-selection
message every gate subcommand prints, the coverage report name the CLI,
``--run-tests`` and ``mutation`` share, and the console output format
default shared by ``memory`` and ``similarity``.
"""

NO_FILES_MESSAGE = "No Python files to check."
DEFAULT_COVERAGE = "coverage.json"
DEFAULT_FORMAT = "console"
