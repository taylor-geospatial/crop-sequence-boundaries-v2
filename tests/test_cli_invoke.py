"""Tests for CLI commands that actually invoke stages (with mocked backends)."""

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from csb.cli import main


def test_polygonize_invokes_run_polygonize(tmp_path: Path) -> None:
    runner = CliRunner()
    output_dir = str(tmp_path / "polygonize_out")
    cdl_dir = tmp_path / "cdl"
    cdl_dir.mkdir()

    with patch("csb.polygonize.run_polygonize", return_value=Path(output_dir)) as mock:
        result = runner.invoke(
            main,
            [
                "polygonize",
                "2020",
                "2022",
                "-o",
                output_dir,
                "--national-cdl-dir",
                str(cdl_dir),
            ],
        )

    assert result.exit_code == 0, result.output
    mock.assert_called_once()
    kwargs = mock.call_args.kwargs
    assert kwargs["start_year"] == 2020
    assert kwargs["end_year"] == 2022


def test_postprocess_invokes_run_postprocess(tmp_path: Path) -> None:
    runner = CliRunner()
    polygonize_dir = tmp_path / "polygonize"
    polygonize_dir.mkdir()
    output_dir = str(tmp_path / "postprocess_out")

    with patch("csb.postprocess.run_postprocess", return_value=Path(output_dir)) as mock:
        result = runner.invoke(
            main,
            [
                "postprocess",
                "2020",
                "2022",
                "--polygonize-dir",
                str(polygonize_dir),
                "-o",
                output_dir,
            ],
        )

    assert result.exit_code == 0, result.output
    mock.assert_called_once()
    kwargs = mock.call_args.kwargs
    assert kwargs["start_year"] == 2020
    assert kwargs["end_year"] == 2022


def test_run_all_invokes_all_stages(tmp_path: Path) -> None:
    runner = CliRunner()
    output_dir = str(tmp_path / "all_out")
    cdl_dir = tmp_path / "cdl"
    cdl_dir.mkdir()

    with (
        patch(
            "csb.polygonize.run_polygonize", return_value=Path(tmp_path / "polygonize")
        ) as mock_poly,
        patch(
            "csb.postprocess.run_postprocess", return_value=Path(tmp_path / "postprocess")
        ) as mock_post,
    ):
        result = runner.invoke(
            main,
            [
                "run-all",
                "2020",
                "2022",
                "-o",
                output_dir,
                "--national-cdl-dir",
                str(cdl_dir),
            ],
        )

    assert result.exit_code == 0, result.output
    mock_poly.assert_called_once()
    mock_post.assert_called_once()
    assert mock_poly.call_args.kwargs["start_year"] == 2020
    assert mock_poly.call_args.kwargs["end_year"] == 2022


def test_polygonize_uses_default_output(tmp_path: Path) -> None:
    """When no -o is given, output path is derived from start/end year."""
    runner = CliRunner()
    cdl_dir = tmp_path / "cdl"
    cdl_dir.mkdir()

    with patch("csb.polygonize.run_polygonize", return_value=Path("/tmp/out")) as mock:
        result = runner.invoke(
            main, ["polygonize", "2020", "2022", "--national-cdl-dir", str(cdl_dir)]
        )

    assert result.exit_code == 0, result.output
    mock.assert_called_once()
    out_path = mock.call_args.kwargs["output_dir"]
    assert "polygonize" in str(out_path)
    assert "2020_2022" in str(out_path)
