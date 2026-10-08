import json
from pathlib import Path

def submission_payload(a_invariants, points, bad_primes=None):
    out = {
        "a_invariants": [str(x) for x in a_invariants],
        "points": [[str(x), str(y)] for x, y in points],
    }
    if bad_primes is not None:
        out["bad_primes"] = [int(p) for p in bad_primes]
    return out

def write_submission(path, a_invariants, points, bad_primes=None):
    Path(path).write_text(
        json.dumps(
            submission_payload(a_invariants, points, bad_primes),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
