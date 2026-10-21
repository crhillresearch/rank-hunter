import numpy as np

from rank42.holes import top_half_cosets


def test_identity_lattice_half_cosets():
    G = np.eye(3)
    rows = top_half_cosets(G, top=7, method="babai")
    assert len(rows) == 7
    assert rows[0]["bits"] == "111"
    assert abs(rows[0]["norm2"] - 0.75) < 1e-12
    norms = [r["norm2"] for r in rows]
    assert norms == sorted(norms, reverse=True)


def test_raw_identity_matches_expected():
    G = np.eye(2)
    rows = top_half_cosets(G, top=3, method="raw")
    got = {r["bits"]: r["norm2"] for r in rows}
    assert abs(got["11"] - 0.5) < 1e-12
    assert abs(got["10"] - 0.25) < 1e-12
    assert abs(got["01"] - 0.25) < 1e-12
