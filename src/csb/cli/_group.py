"""Shared Click group and helpers for the ``csb`` CLI."""

import click
from rich.console import Console

from csb.config import DEFAULT_ELIMINATE_THRESHOLDS

console = Console()

_DEFAULT_THRESHOLDS_STR = ",".join(str(int(t)) for t in DEFAULT_ELIMINATE_THRESHOLDS)


def _parse_thresholds(
    _ctx: click.Context, _param: click.Parameter, value: str
) -> tuple[float, ...]:
    """Click callback: parse a comma-separated list of floats."""
    if not value:
        return DEFAULT_ELIMINATE_THRESHOLDS
    try:
        return tuple(float(x.strip()) for x in value.split(","))
    except ValueError as e:
        msg = f"--eliminate-thresholds must be a comma-separated list of numbers, got {value!r}"
        raise click.BadParameter(msg) from e


@click.group()
def main() -> None:
    """CSB — open-source Crop Sequence Boundaries pipeline."""
