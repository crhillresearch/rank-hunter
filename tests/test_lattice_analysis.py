import json

from rank42.lattice_analysis import analyze_lattice


def _row(gram, *, eigenvalues, determinant="3", positive=1):
    return {
        "gram_json": json.dumps(gram),
        "basis_json": json.dumps([["0", "1"] for _ in gram]),
        "metadata_json": json.dumps({"eigenvalues": [str(x) for x in eigenvalues]}),
        "determinant": determinant,
        "min_eigenvalue": str(min(eigenvalues)),
        "positive_definite_screen": positive,
    }


def test_analyze_lattice_reports_geometry():
    result = analyze_lattice(_row([[2, 1], [1, 2]], eigenvalues=[1, 3]))

    assert result["basis_count"] == 2
    assert result["positive_directions"] == 2
    assert result["positive_definite_screen"] is True
    assert result["condition"] == 3
    assert result["strongest_pair"]["left"] == 0
    assert result["strongest_pair"]["right"] == 1
    assert result["strongest_pair"]["correlation"] == 0.5
    assert result["generator_rows"][0]["closest_generator"] == "P2"
    assert result["status"] == "Healthy"


def test_analyze_lattice_flags_nonpositive_direction():
    result = analyze_lattice(_row([[1, 0], [0, -1]], eigenvalues=[-1, 1], determinant="-1", positive=0))

    assert result["positive_directions"] == 1
    assert result["condition"] is None
    assert result["status"] == "Possible dependence"
    assert result["status_level"] == "danger"
