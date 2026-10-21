"""Data-only scaffolding for future Mordell--Weil lattice-hole searches.

v0.4.1 does not invent the missing rank-17 K3 lattice or a quartic-cover map.
Instead it standardizes the handoff record that a future lattice module must
produce: a hole/coset label plus an exact quartic y^2=f(x) to send to
``rank42.quartic_search``.
"""

from __future__ import annotations

import json
from pathlib import Path


def make_hole_job(*, coefficients, height, hole_label, curve_id=None, metadata=None):
    return {
        "schema": "rank42.hole-quartic-job.v1",
        "hole_label": str(hole_label),
        "curve_id": int(curve_id) if curve_id is not None else None,
        "coefficients": [str(x) for x in coefficients],
        "height": int(height),
        "metadata": dict(metadata or {}),
    }


def write_hole_job(path, **kwargs):
    data = make_hole_job(**kwargs)
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data


def validate_hole_job(data):
    if data.get("schema") != "rank42.hole-quartic-job.v1":
        raise ValueError("unsupported hole job schema")
    if not data.get("hole_label"):
        raise ValueError("hole_label is required")
    if not data.get("coefficients"):
        raise ValueError("coefficients are required")
    if int(data.get("height", 0)) <= 0:
        raise ValueError("height must be positive")
    return data
