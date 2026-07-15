"""Tests for csb.cli."""

from click.testing import CliRunner

from csb.cli import main


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Crop Sequence Boundaries" in result.output


def test_polygonize_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["polygonize", "--help"])
    assert result.exit_code == 0
    assert "start_year" in result.output.lower()


def test_postprocess_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["postprocess", "--help"])
    assert result.exit_code == 0
    assert "--polygonize-dir" in result.output


def test_run_all_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["run-all", "--help"])
    assert result.exit_code == 0
    assert "polygonize" in result.output.lower()
    assert "postprocess" in result.output.lower()
