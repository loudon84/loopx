"""Tests for the DSH CLI runtime availability probe.

The probe is the gate that decides whether a Coding Worker Turn may start, so
each required check is exercised in both directions. The ``dsh_home_writable``
check is the one Phase 0 proved is not optional: the launcher rewrites
``profiles/<name>/cordis.yml`` on every boot, including ``--help``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from loopx.dsh_goal_mode.dsh_cli_probe import DshCliRuntime, probe_dsh_cli
from tests.dsh_cli_fake_launcher import write_fake_launcher


@pytest.fixture()
def launcher(tmp_path: Path) -> Path:
    return write_fake_launcher(tmp_path)


@pytest.fixture()
def writable_home(tmp_path: Path) -> Path:
    home = tmp_path / "dsh-home"
    home.mkdir()
    return home


@pytest.fixture()
def credentialed_home(writable_home: Path) -> Path:
    """A home that also carries a credential store, as the real host does."""

    (writable_home / ".credentials.yaml").write_text("deepseek: test\n")
    return writable_home


def _probe(
    launcher: Path,
    tmp_path: Path,
    home: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> dict:
    return probe_dsh_cli(
        DshCliRuntime(
            dsh_bin=str(launcher),
            dsh_home=home,
            workspace=tmp_path,
            extra_env=extra_env or {},
        )
    )


def _check(result: dict, name: str) -> dict:
    return next(check for check in result["checks"] if check["name"] == name)


def test_ready_when_every_required_check_passes(
    launcher: Path, tmp_path: Path, credentialed_home: Path
) -> None:
    result = _probe(launcher, tmp_path, credentialed_home)

    assert result["ready"] is True
    assert result["reason"] == "ready"
    assert _check(result, "version")["ok"] is True
    assert _check(result, "headless_profile")["ok"] is True
    assert _check(result, "dsh_home_writable")["ok"] is True
    assert _check(result, "credentials")["ok"] is True


def test_capability_document_reports_measured_tools_not_assumptions(
    launcher: Path, tmp_path: Path, credentialed_home: Path
) -> None:
    capability = _probe(launcher, tmp_path, credentialed_home)["capability"]

    assert capability["schema_version"] == "loopx_dsh_coding_worker_capability_v1"
    assert capability["worker_kind"] == "dsh_coding_worker"
    assert capability["session_ownership"] == "managed_runtime"
    capabilities = capability["capabilities"]
    assert capabilities["shell"] is True
    assert capabilities["file_read"] is True
    assert capabilities["git"] is True
    # Unmeasured capabilities must never be upgraded to True.
    assert capabilities["session_resume"] == "probe_required"


def test_version_outside_the_supported_line_blocks_readiness(
    launcher: Path, tmp_path: Path, writable_home: Path
) -> None:
    result = _probe(launcher, tmp_path, writable_home, extra_env={"FAKE_DSH_VERSION": "0.3.0"})

    assert result["ready"] is False
    assert "version" in result["reason"]


def test_version_on_the_line_but_unmeasured_is_flagged(
    launcher: Path, tmp_path: Path, credentialed_home: Path
) -> None:
    result = _probe(
        launcher, tmp_path, credentialed_home, extra_env={"FAKE_DSH_VERSION": "0.2.9"}
    )

    assert result["ready"] is True
    assert "not the measured" in _check(result, "version")["detail"]


def test_read_only_dsh_home_blocks_readiness(
    launcher: Path, tmp_path: Path
) -> None:
    home = tmp_path / "readonly-home"
    home.mkdir()
    home.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = _probe(launcher, tmp_path, home)

        assert result["ready"] is False
        assert _check(result, "dsh_home_writable")["ok"] is False
        assert "dsh_home_writable" in result["reason"]
    finally:
        home.chmod(stat.S_IRWXU)


def test_missing_binary_blocks_readiness(tmp_path: Path, writable_home: Path) -> None:
    result = probe_dsh_cli(
        DshCliRuntime(
            dsh_bin=str(tmp_path / "not-a-real-dsh"),
            dsh_home=writable_home,
            workspace=tmp_path,
        )
    )

    assert result["ready"] is False
    assert _check(result, "binary_present")["ok"] is False
    assert result["capability"]["capabilities"]["shell"] is False


def test_absent_credentials_block_readiness(
    launcher: Path, tmp_path: Path, writable_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    result = _probe(launcher, tmp_path, writable_home)

    assert result["ready"] is False
    assert _check(result, "credentials")["ok"] is False
    assert "credentials" in result["reason"]


def test_environment_credential_satisfies_the_check(
    launcher: Path, tmp_path: Path, writable_home: Path
) -> None:
    result = _probe(
        launcher, tmp_path, writable_home, extra_env={"DEEPSEEK_API_KEY": "test-key"}
    )

    assert _check(result, "credentials")["ok"] is True
    assert _check(result, "credentials")["detail"] == "DEEPSEEK_API_KEY present in the host env"


def test_probe_detail_is_public_safe(
    launcher: Path, tmp_path: Path, writable_home: Path
) -> None:
    result = _probe(
        launcher, tmp_path, writable_home, extra_env={"DEEPSEEK_API_KEY": "sk-super-secret"}
    )

    assert "sk-super-secret" not in str(result)


def test_os_error_on_unwritable_home_reports_the_cause(
    launcher: Path, tmp_path: Path
) -> None:
    home = tmp_path / "file-not-dir"
    home.write_text("this is a file, not a directory")

    result = _probe(launcher, tmp_path, home)

    assert result["ready"] is False
    assert _check(result, "dsh_home_writable")["ok"] is False
    assert os.path.isdir(home) is False
