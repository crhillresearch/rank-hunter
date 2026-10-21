import json
import subprocess
import sys
import time

MARKER = "RANK42_JSON="

class DescentTimeout(RuntimeError):
    failure_class = "TIMEOUT"

    def __init__(self, message, *, runtime=None):
        super().__init__(message)
        self.runtime = runtime

class DescentFailure(RuntimeError):
    def __init__(
        self,
        message,
        *,
        failure_class="WORKER_FAILURE",
        runtime=None,
        returncode=None,
        tail=None,
    ):
        super().__init__(message)
        self.failure_class = failure_class
        self.runtime = runtime
        self.returncode = returncode
        self.tail = tail

def classify_failure(returncode, tail):
    text = (tail or "").lower()
    if returncode is not None and returncode < 0:
        sig = -int(returncode)
        if sig == 9:
            return "KILLED"
        if sig == 11:
            return "SEGFAULT"
        return f"SIGNAL_{sig}"
    if any(s in text for s in ("memoryerror", "out of memory", "cannot allocate memory")):
        return "OOM"
    if "segmentation fault" in text:
        return "SEGFAULT"
    if "pari" in text:
        return "PARI_ERROR"
    if "attempt to convert" in text and "to long fails" in text and "too large" in text:
        return "MWRANK_SIZE_LIMIT"
    if "mwrank" in text or "two_descent" in text or "2-descent" in text:
        return "MWRANK_FAILURE"
    if "json" in text:
        return "WORKER_PROTOCOL"
    return "WORKER_FAILURE"

def run_descent(payload, timeout):
    cmd = [sys.executable, "-m", "rank42.descent_worker"]
    started = time.monotonic()
    try:
        cp = subprocess.run(
            cmd,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        runtime = time.monotonic() - started
        raise DescentTimeout(
            f"descent exceeded {timeout}s",
            runtime=runtime,
        ) from exc

    runtime = time.monotonic() - started
    lines = (cp.stdout or "").splitlines()
    result_line = None
    for line in reversed(lines):
        if line.startswith(MARKER):
            result_line = line[len(MARKER):]
            break

    tail = "\n".join(
        ((cp.stdout or "") + "\n" + (cp.stderr or "")).splitlines()[-30:]
    )

    if cp.returncode != 0 or result_line is None:
        failure_class = classify_failure(cp.returncode, tail)
        raise DescentFailure(
            f"worker failed with exit={cp.returncode}; tail:\n{tail}",
            failure_class=failure_class,
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        )

    try:
        result = json.loads(result_line)
    except json.JSONDecodeError as exc:
        raise DescentFailure(
            f"worker returned invalid result JSON: {exc}; tail:\n{tail}",
            failure_class="WORKER_PROTOCOL",
            runtime=runtime,
            returncode=cp.returncode,
            tail=tail,
        ) from exc

    result["_runtime_seconds"] = runtime
    return result
