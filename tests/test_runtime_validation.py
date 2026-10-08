import os
from pathlib import Path

from rank42.runtime_validation import (
    probe_runtime_bundle,
    probe_runtime_setting,
    required_runtime_failures,
    resolve_configured_executable,
)


def _fake_executable(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(
        "#!/usr/bin/env python3\n" + body,
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_runtime_resolution_requires_executable_permission(tmp_path):
    path = tmp_path / "not-executable"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    assert resolve_configured_executable(path) is None

    path.chmod(0o755)
    assert resolve_configured_executable(path) == str(path.resolve())


def test_science_runtime_probe_requires_successful_sage_import(tmp_path):
    good = _fake_executable(
        tmp_path,
        "science-good",
        "print('10.6')\n",
    )
    bad = _fake_executable(
        tmp_path,
        "science-bad",
        "import sys\nprint('no sage here', file=sys.stderr)\nraise SystemExit(2)\n",
    )

    passed = probe_runtime_setting("science_python", good)
    failed = probe_runtime_setting("science_python", bad)

    assert passed.healthy is True
    assert passed.status == "pass"
    assert passed.version == "10.6"
    assert failed.healthy is False
    assert failed.status == "fail"
    assert "no sage here" in failed.detail


def test_ratpoints_probe_requires_ratpoints_identity(tmp_path):
    good = _fake_executable(
        tmp_path,
        "ratpoints-good",
        "import sys\nprint('This is ratpoints-2.2.2', file=sys.stderr)\n"
        "raise SystemExit(1)\n",
    )
    wrong = _fake_executable(
        tmp_path,
        "ratpoints-wrong",
        "print('totally different tool')\n",
    )

    passed = probe_runtime_setting("ratpoints", good)
    failed = probe_runtime_setting("ratpoints", wrong)

    assert passed.healthy is True
    assert passed.status == "pass"
    assert passed.version == "2.2.2"
    assert failed.healthy is False
    assert failed.status == "fail"
    assert "did not identify itself as ratpoints" in failed.detail


def test_missing_gpu_is_optional_warning_but_required_runtime_failure_is_blocking(
    tmp_path,
):
    missing = tmp_path / "missing-runtime"

    science = probe_runtime_setting("science_python", missing)
    cpu = probe_runtime_setting("ratpoints", missing)
    gpu = probe_runtime_setting("ratpoints_gpu", missing)

    assert science.status == "fail"
    assert cpu.status == "fail"
    assert gpu.status == "warn"
    assert gpu.required is False

    failures = required_runtime_failures({
        "science_python": science,
        "ratpoints": cpu,
        "ratpoints_gpu": gpu,
    })
    assert [probe.key for probe in failures] == [
        "science_python",
        "ratpoints",
    ]


def test_runtime_bundle_preserves_optional_gpu_warning(tmp_path):
    science = _fake_executable(
        tmp_path,
        "science",
        "print('10.6')\n",
    )
    cpu = _fake_executable(
        tmp_path,
        "ratpoints",
        "import sys\nprint('This is ratpoints-2.2.2', file=sys.stderr)\n"
        "raise SystemExit(1)\n",
    )

    probes = probe_runtime_bundle(
        science,
        cpu,
        tmp_path / "missing-gpu",
    )

    assert required_runtime_failures(probes) == []
    assert probes["science_python"].healthy is True
    assert probes["ratpoints"].healthy is True
    assert probes["ratpoints_gpu"].healthy is False
    assert probes["ratpoints_gpu"].status == "warn"
