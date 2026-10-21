from pathlib import Path

from rank42 import server_control


def test_restart_server_waits_for_current_process_before_launching_replacement(
    tmp_path,
    monkeypatch,
):
    script = tmp_path / "scripts" / "run-ui.sh"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    db_path = tmp_path / "rank42.db"
    runtime = tmp_path / "sage-python"

    captured = {}

    class FakeProcess:
        pid = 4321

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(server_control.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_control.os, "getpid", lambda: 2468)

    child_pid = server_control.restart_server(
        tmp_path,
        db_path,
        runtime_python=runtime,
        delay_seconds=0.25,
        shutdown_timeout_seconds=2.0,
    )

    assert child_pid == 4321
    command = captured["command"]
    assert command[:4] == [
        "/bin/bash",
        "-c",
        command[2],
        "rank-hunter-restart",
    ]
    helper = command[2]
    assert 'kill -TERM "$old_pid"' in helper
    assert 'while kill -0 "$old_pid"' in helper
    assert 'kill -KILL "$old_pid"' in helper
    assert 'exec /bin/bash "$launcher"' in helper
    assert helper.index('while kill -0 "$old_pid"') < helper.index(
        'exec /bin/bash "$launcher"'
    )
    assert command[4:] == [
        "2468",
        "0.25",
        "8",
        str(script.resolve()),
    ]
    assert captured["kwargs"]["cwd"] == str(tmp_path.resolve())
    assert captured["kwargs"]["env"]["RANK_HUNTER_DB"] == str(db_path.resolve())
    assert captured["kwargs"]["env"]["RANK_HUNTER_RUNTIME_PYTHON"] == str(
        runtime.resolve()
    )
    assert captured["kwargs"]["start_new_session"] is True


def test_restart_server_requires_canonical_launcher(tmp_path):
    try:
        server_control.restart_server(tmp_path, tmp_path / "rank42.db")
    except FileNotFoundError as exc:
        assert "scripts/run-ui.sh" in str(exc)
    else:
        raise AssertionError("restart must refuse to invent a launcher")


def test_shutdown_server_uses_detached_bounded_watchdog(monkeypatch):
    captured = {}

    class FakeProcess:
        pid = 9753

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(server_control.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_control.os, "getpid", lambda: 1357)

    child_pid = server_control.shutdown_server(
        delay_seconds=0.25,
        shutdown_timeout_seconds=2.0,
    )

    assert child_pid == 9753
    command = captured["command"]
    assert command[:4] == [
        "/bin/bash",
        "-c",
        command[2],
        "rank-hunter-shutdown",
    ]
    helper = command[2]
    assert 'kill -TERM "$old_pid"' in helper
    assert 'while kill -0 "$old_pid"' in helper
    assert 'kill -KILL "$old_pid"' in helper
    assert "launcher" not in helper
    assert "exec " not in helper
    assert command[4:] == ["1357", "0.25", "8"]
    assert captured["kwargs"]["stdin"] is server_control.subprocess.DEVNULL
    assert captured["kwargs"]["stdout"] is server_control.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is server_control.subprocess.DEVNULL
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["close_fds"] is True


def test_shutdown_watchdog_escalates_only_after_bounded_wait(monkeypatch):
    captured = {}

    class FakeProcess:
        pid = 8642

    def fake_popen(command, **kwargs):
        captured["helper"] = command[2]
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr(server_control.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_control.os, "getpid", lambda: 2468)

    server_control.shutdown_server(
        delay_seconds=0.1,
        shutdown_timeout_seconds=0.35,
    )

    helper = captured["helper"]
    assert helper.index('kill -TERM "$old_pid"') < helper.index(
        'while kill -0 "$old_pid"'
    )
    assert helper.index('while kill -0 "$old_pid"') < helper.index(
        'kill -KILL "$old_pid"'
    )
    assert captured["command"][4:] == ["2468", "0.1", "3"]
