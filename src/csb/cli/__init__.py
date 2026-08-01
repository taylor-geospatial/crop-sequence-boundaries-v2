"""Command-line entrypoint for the ``csb`` package.

Importing the submodules registers their commands on the ``main`` group;
each submodule imports ``main`` from ``csb.cli._group`` itself, so the
import order here does not matter.
"""

from csb.cli import eval, experiments, pipeline, serve
from csb.cli._group import _parse_thresholds, console, main

__all__ = [
    "_parse_thresholds",
    "console",
    "eval",
    "experiments",
    "main",
    "pipeline",
    "serve",
]
