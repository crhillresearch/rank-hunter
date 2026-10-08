import os
from fractions import Fraction
from pathlib import Path

import pytest

from rank42.ratpoints import (
    RatpointsTimeout,
    evaluate_polynomial,
    normalize_polynomial,
    run_ratpoints,
)


def make_fake(tmp_path, body):
    p = tmp_path / "ratpoints"
    p.write_text("#!/usr/bin/env python3\n" + body)
    p.chmod(0o755)
    return str(p)


def test_normalize_rational_quartic():
    n = normalize_polynomial(["1/2", "0", "1/3", "0", "1"])
    assert n.y_scale == 6
    assert n.integer_coefficients == (18, 0, 12, 0, 36)
    assert n.degree == 4


def test_fake_ratpoints_parse_and_exact_check(tmp_path):
    # Original curve y^2=x^4+1 has point (0,1).  Degree four means the
    # projective y-coordinate has weight two; z=1 keeps the smoke point simple.
    exe = make_fake(
        tmp_path,
        """
import sys
if len(sys.argv) < 3:
    print('This is ratpoints-2.2.2', file=sys.stderr)
    raise SystemExit(1)
print('0\\t1\\t1')
""",
    )
    r = run_ratpoints([1, 0, 0, 0, 1], 100, executable=exe, timeout=5)
    assert len(r["points"]) == 1
    P = r["points"][0]
    assert P.x == 0
    assert P.y == 1
    assert evaluate_polynomial([1, 0, 0, 0, 1], P.x) == P.y * P.y


def test_rational_coefficient_y_rescaling(tmp_path):
    # y^2 = 1/4 + x^4, point (0, 1/2).  D=4 for the coefficient
    # denominators, so ratpoints sees Y=2 on Y^2=4+16x^4.
    exe = make_fake(
        tmp_path,
        """
import sys
if len(sys.argv) < 3:
    print('This is ratpoints-2.2.2', file=sys.stderr)
    raise SystemExit(1)
print('0\\t2\\t1')
""",
    )
    r = run_ratpoints(["1/4", 0, 0, 0, 1], 20, executable=exe, timeout=5)
    P = r["points"][0]
    assert P.x == 0
    assert P.y == Fraction(1, 2)


def test_timeout(tmp_path):
    exe = make_fake(
        tmp_path,
        """
import sys, time
if len(sys.argv) < 3:
    print('This is ratpoints-2.2.2', file=sys.stderr)
    raise SystemExit(1)
time.sleep(2)
""",
    )
    with pytest.raises(RatpointsTimeout):
        run_ratpoints([1, 0, 0, 0, 1], 20, executable=exe, timeout=0.05)


def test_short_weierstrass_cubic_point_for_high_rank_playground(tmp_path):
    exe = make_fake(
        tmp_path,
        """
import sys
if len(sys.argv) < 3:
    print('This is ratpoints-2.2.2', file=sys.stderr)
    raise SystemExit(1)
print('0\\t1\\t1')
""",
    )
    # y^2 = x^3 + 14*x + 1; the high-rank Playground supplies [B,A,0,1].
    r = run_ratpoints([1, 14, 0, 1], 100, executable=exe, timeout=5)
    assert len(r["points"]) == 1
    assert r["points"][0].x == 0
    assert r["points"][0].y == 1


def _process_is_running(pid):
    stat = Path(f"/proc/{int(pid)}/stat")
    try:
        fields = stat.read_text().split()
    except OSError:
        return False
    return len(fields) > 2 and fields[2] != 'Z'


def test_successful_gpu_style_wrapper_does_not_leave_helper_process(tmp_path):
    pidfile = tmp_path / "helper.pid"
    exe = make_fake(
        tmp_path,
        f"""
import subprocess, sys
if len(sys.argv) < 3:
    print('This is ratpoints-2.2.2', file=sys.stderr)
    raise SystemExit(1)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
open({str(pidfile)!r}, 'w').write(str(child.pid))
print('0\\t1\\t1')
""",
    )
    result = run_ratpoints([1, 0, 0, 0, 1], 20, executable=exe, timeout=5)
    assert len(result["points"]) == 1
    helper_pid = int(pidfile.read_text())
    deadline = __import__('time').monotonic() + 2.0
    while _process_is_running(helper_pid) and __import__('time').monotonic() < deadline:
        __import__('time').sleep(0.02)
    assert not _process_is_running(helper_pid)



def test_timeout_gpu_style_wrapper_does_not_leave_helper_process(tmp_path):
    pidfile = tmp_path / "timeout-helper.pid"
    exe = make_fake(
        tmp_path,
        f"""
import subprocess, sys, time
if len(sys.argv) < 3:
    print('This is ratpoints-2.2.2', file=sys.stderr)
    raise SystemExit(1)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
open({str(pidfile)!r}, 'w').write(str(child.pid))
time.sleep(60)
""",
    )
    with pytest.raises(RatpointsTimeout) as caught:
        run_ratpoints([1, 0, 0, 0, 1], 20, executable=exe, timeout=0.1)

    assert pidfile.is_file()
    helper_pid = int(pidfile.read_text())
    deadline = __import__('time').monotonic() + 2.0
    while _process_is_running(helper_pid) and __import__('time').monotonic() < deadline:
        __import__('time').sleep(0.02)
    assert not _process_is_running(helper_pid)

    provenance = caught.value.engine_provenance
    assert provenance["status"] == "timeout"
    assert provenance["executable"] == str(Path(exe).resolve())
    assert provenance["version"] == "2.2.2"
    assert provenance["argv"][0] == str(Path(exe).resolve())
    assert provenance["timeout_seconds"] == 0.1
    assert provenance["runtime_seconds"] >= 0.0


def test_successful_run_returns_engine_invocation_provenance(tmp_path):
    exe = make_fake(
        tmp_path,
        """
import sys
if len(sys.argv) < 3:
    print('This is ratpoints-2.2.2', file=sys.stderr)
    raise SystemExit(1)
print('0\\t1\\t1')
""",
    )
    result = run_ratpoints(
        [1, 0, 0, 0, 1],
        20,
        executable=exe,
        timeout=5,
        denominator_low=2,
        denominator_high=10,
    )
    provenance = result["engine_provenance"]
    assert provenance["status"] == "completed"
    assert provenance["backend"] == "CPU"
    assert provenance["version"] == "2.2.2"
    assert provenance["executable"] == str(Path(exe).resolve())
    assert provenance["argv"][-4:] == ["-dl", "2", "-du", "10"]
    assert provenance["timeout_seconds"] == 5.0
    assert provenance["runtime_seconds"] == result["runtime"]
