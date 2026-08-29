import os
import subprocess
import sys
from pathlib import Path

import pytest

from batchmortal.tenhou import parse_tenhou_log_id
from main import collect_file_tasks, parse_args


MAJSOUL_URL = (
    "https://game.maj-soul.com/1/?paipu="
    "260829-e70ff6e7-e2fa-4545-9022-a550b4dbc42f_a12345678"
)
TENHOU_URL = (
    "https://tenhou.net/0/?log=2019050417gm-0029-0000-4f2a8622&tw=2"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_file_can_be_combined_with_explicit_source_mode(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--mode", "th", "--file", "paipu.txt", "-p", "reviewer"],
    )

    args = parse_args()

    assert args.file == "paipu.txt"
    assert args.mode == "th"
    assert args.source == "tenhou"


@pytest.mark.parametrize(
    ("source", "url", "expected_uuid", "expected_mode", "expected_start"),
    [
        (
            "majsoul",
            MAJSOUL_URL,
            "260829-e70ff6e7-e2fa-4545-9022-a550b4dbc42f",
            "file",
            "2026-08-29",
        ),
        (
            "tenhou",
            TENHOU_URL,
            "2019050417gm-0029-0000-4f2a8622",
            "4p-south",
            parse_tenhou_log_id("2019050417gm-0029-0000-4f2a8622")["start_time"],
        ),
    ],
)
def test_file_tasks_use_link_metadata_and_deduplicate_current_file(
    tmp_path,
    source,
    url,
    expected_uuid,
    expected_mode,
    expected_start,
):
    link_file = tmp_path / "paipu.txt"
    link_file.write_text(f"{url}\n\n{url}\n", encoding="utf-8")

    tasks = collect_file_tasks(str(link_file), source, str(tmp_path), set())

    assert len(tasks) == 1
    assert tasks[0]["uuid"] == expected_uuid
    assert tasks[0]["mode"] == expected_mode
    assert tasks[0]["start_time"] == expected_start
    assert tasks[0]["end_time"] == ""
    assert tasks[0]["mode_dir"] == os.path.join(str(tmp_path), f"mode_{expected_mode}")


def test_file_source_mismatch_is_fatal_before_archiving(tmp_path):
    link_file = tmp_path / "paipu.txt"
    link_file.write_text(MAJSOUL_URL, encoding="utf-8")

    with pytest.raises(ValueError, match=r"selected source is Tenhou.*--mode mj"):
        collect_file_tasks(str(link_file), "tenhou", str(tmp_path), set())


def test_unreadable_file_is_reported_as_a_controlled_error(tmp_path):
    missing = tmp_path / "missing.txt"

    with pytest.raises(ValueError, match="Could not read paipu link file"):
        collect_file_tasks(str(missing), "majsoul", str(tmp_path), set())


def test_cli_reports_file_errors_without_a_traceback(tmp_path):
    missing = tmp_path / "missing.txt"

    result = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "main.py"),
            "--mode",
            "mj",
            "--file",
            str(missing),
            "-p",
            "reviewer",
            "--dry-run",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Could not read paipu link file" in result.stderr
    assert "Traceback" not in result.stderr
