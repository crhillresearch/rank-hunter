import json

from rank42.formula_family import FormulaFamily


def test_formula_family_preserves_section_labels_vectors_and_trace(tmp_path):
    path = tmp_path / "family.json"
    path.write_text(json.dumps({
        "name": "metadata family",
        "generic_rank": 1,
        "parameter": "t",
        "a_invariants": ["0", "0", "0", "-1", "0"],
        "sections": [{
            "x": "0",
            "y": "0",
            "basis_label": "S1",
            "coefficient_vector": [1],
            "search_label": "1*S1",
        }],
    }))
    family = FormulaFamily.from_json(path)
    points = family.generic_section_points(3)
    metadata = family.generic_section_metadata(3)

    assert len(points) == 1
    assert metadata == [{
        "section_index": 0,
        "basis_label": "S1",
        "coefficient_vector": [1],
        "search_label": "1*S1",
        "trace_coordinates": ["0", "0"],
    }]
