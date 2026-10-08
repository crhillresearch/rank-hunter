from __future__ import annotations

import json
import math


def _value(row, key, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        value = default
    return default if value is None else value


def _loads(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _number(value):
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _matrix_numbers(raw):
    matrix = _loads(raw, [])
    out = []
    for row in matrix:
        out.append([float(value) for value in row])
    if out and any(len(row) != len(out) for row in out):
        raise ValueError("height Gram matrix must be square")
    return out


def _fallback_eigenvalues(gram):
    if not gram:
        return []
    import numpy as np

    values = np.linalg.eigvalsh(np.asarray(gram, dtype=float))
    return [float(value) for value in values]


def _eigenvalues(gram, metadata):
    stored = metadata.get("eigenvalues") or metadata.get("reduced_eigenvalues")
    values = []
    if isinstance(stored, list) and len(stored) == len(gram):
        for value in stored:
            number = _number(value)
            if number is None:
                values = []
                break
            values.append(number)
    if not values:
        values = _fallback_eigenvalues(gram)
    return sorted(values, reverse=True)


def _correlations(gram):
    n = len(gram)
    correlations = [[0.0 for _ in range(n)] for _ in range(n)]
    pairs = []
    for i in range(n):
        correlations[i][i] = 1.0 if gram[i][i] > 0 else 0.0
        for j in range(i + 1, n):
            denom = gram[i][i] * gram[j][j]
            corr = gram[i][j] / math.sqrt(denom) if denom > 0 else 0.0
            if not math.isfinite(corr):
                corr = 0.0
            corr = max(-1.0, min(1.0, corr))
            correlations[i][j] = corr
            correlations[j][i] = corr
            pairs.append(
                {
                    "left": i,
                    "right": j,
                    "correlation": corr,
                    "absolute_correlation": abs(corr),
                    "pairing": gram[i][j],
                }
            )
    pairs.sort(key=lambda item: (item["absolute_correlation"], item["left"], item["right"]), reverse=True)
    return correlations, pairs


def _health(*, size, positive_directions, condition, weakest_ratio):
    if size == 0:
        return "No basis", "empty"
    if positive_directions < size:
        return "Possible dependence", "danger"
    if condition is None:
        return "Unresolved", "warning"
    if weakest_ratio is not None and weakest_ratio < 1e-10:
        return "Near-degenerate", "danger"
    if condition >= 1e8:
        return "Poor conditioning", "warning"
    if condition >= 1e5:
        return "Watch", "warning"
    return "Healthy", "success"


def analyze_lattice(row):
    gram = _matrix_numbers(_value(row, "gram_json", "[]"))
    metadata = _loads(_value(row, "metadata_json", "{}"), {})
    basis = _loads(_value(row, "basis_json", "[]"), [])
    n = len(gram)
    eigenvalues = _eigenvalues(gram, metadata)
    max_eigenvalue = max(eigenvalues) if eigenvalues else None
    min_eigenvalue = min(eigenvalues) if eigenvalues else None
    positive_directions = sum(1 for value in eigenvalues if value > 0)
    condition = None
    if max_eigenvalue is not None and min_eigenvalue is not None and min_eigenvalue > 0:
        condition = max_eigenvalue / min_eigenvalue
    weakest_ratio = None
    if max_eigenvalue is not None and max_eigenvalue > 0 and min_eigenvalue is not None:
        weakest_ratio = min_eigenvalue / max_eigenvalue

    correlations, pairs = _correlations(gram)
    strongest = pairs[0] if pairs else None
    diagonals = [gram[i][i] for i in range(n)]
    max_height = max(diagonals) if diagonals else None
    min_height = min(diagonals) if diagonals else None
    height_skew = None
    if min_height is not None and min_height > 0 and max_height is not None:
        height_skew = max_height / min_height

    determinant = _number(_value(row, "determinant"))
    status, status_level = _health(
        size=n,
        positive_directions=positive_directions,
        condition=condition,
        weakest_ratio=weakest_ratio,
    )

    generator_rows = []
    for i in range(n):
        neighbors = [pair for pair in pairs if pair["left"] == i or pair["right"] == i]
        nearest = max(neighbors, key=lambda item: item["absolute_correlation"]) if neighbors else None
        other = None
        if nearest is not None:
            other = nearest["right"] if nearest["left"] == i else nearest["left"]
        generator_rows.append(
            {
                "generator": f"P{i + 1}",
                "point": basis[i] if i < len(basis) else None,
                "height": diagonals[i],
                "max_correlation": nearest["absolute_correlation"] if nearest else 0.0,
                "closest_generator": f"P{other + 1}" if other is not None else "—",
            }
        )

    features = []
    positive_screen = bool(_value(row, "positive_definite_screen", 0))
    if positive_directions < n:
        features.append(("danger", f"Only {positive_directions} of {n} numerical directions are positive; investigate dependence or precision."))
    elif not positive_screen:
        features.append(("warning", "Stored positive-definite screen and reconstructed eigenstructure disagree; recompute this lattice."))

    if weakest_ratio is not None:
        if weakest_ratio < 1e-10:
            features.append(("danger", f"Weakest direction is only {weakest_ratio:.2e} of the strongest direction."))
        elif weakest_ratio < 1e-6:
            features.append(("warning", f"Lattice is strongly anisotropic: λmin/λmax = {weakest_ratio:.2e}."))

    if strongest is not None:
        pair_label = f"P{strongest['left'] + 1} ↔ P{strongest['right'] + 1}"
        if strongest["absolute_correlation"] >= 0.95:
            features.append(("warning", f"{pair_label} is very strongly correlated (|ρ| = {strongest['absolute_correlation']:.3f})."))
        elif strongest["absolute_correlation"] >= 0.80:
            features.append(("info", f"{pair_label} is the strongest generator relationship (|ρ| = {strongest['absolute_correlation']:.3f})."))

    if not features:
        features.append(("success", "No near-dependence, severe anisotropy, or unusually strong pair correlation is visible in this stored lattice."))

    return {
        "gram": gram,
        "metadata": metadata,
        "basis": basis,
        "basis_count": n,
        "eigenvalues": eigenvalues,
        "positive_directions": positive_directions,
        "max_eigenvalue": max_eigenvalue,
        "min_eigenvalue": min_eigenvalue,
        "condition": condition,
        "weakest_ratio": weakest_ratio,
        "determinant": determinant,
        "correlations": correlations,
        "pairs": pairs,
        "strongest_pair": strongest,
        "generator_rows": generator_rows,
        "max_height": max_height,
        "min_height": min_height,
        "height_skew": height_skew,
        "status": status,
        "status_level": status_level,
        "features": features,
        "positive_definite_screen": positive_screen,
    }


def _solve_linear_system(matrix, vector):
    """Solve a small dense floating-point system with partial pivoting."""
    n = len(matrix)
    if n == 0 or len(vector) != n or any(len(row) != n for row in matrix):
        raise ValueError("linear system must be square")
    aug = [list(map(float, row)) + [float(vector[i])] for i, row in enumerate(matrix)]
    scale = max((abs(value) for row in matrix for value in row), default=1.0)
    tol = max(1e-15, scale * 1e-14)
    for col in range(n):
        pivot = max(range(col, n), key=lambda i: abs(aug[i][col]))
        if abs(aug[pivot][col]) <= tol:
            raise ValueError("basis Gram matrix is numerically singular")
        if pivot != col:
            aug[col], aug[pivot] = aug[pivot], aug[col]
        p = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= p
        for i in range(n):
            if i == col:
                continue
            factor = aug[i][col]
            if factor == 0:
                continue
            for j in range(col, n + 1):
                aug[i][j] -= factor * aug[col][j]
    return [aug[i][n] for i in range(n)]


def point_ledger_candidate_residuals(row):
    """Return numerical candidate residuals against a rigorous Point Ledger basis.

    The result is scheduling evidence only.  It is derived from the stored
    *input* height Gram matrix, before any LLL basis change.
    """
    metadata = _loads(_value(row, "metadata_json"), {})
    if not isinstance(metadata, dict):
        return []
    ledger = metadata.get("point_ledger")
    if not isinstance(ledger, dict):
        return []
    labels = list(ledger.get("input_labels") or [])
    try:
        basis_count = int(ledger.get("rigorous_basis_count") or 0)
    except (TypeError, ValueError):
        return []
    gram = _matrix_numbers(metadata.get("input_gram"))
    if (
        basis_count <= 0
        or not gram
        or len(gram) != len(labels)
        or len(gram) < basis_count
    ):
        return []

    basis_gram = [row[:basis_count] for row in gram[:basis_count]]
    out = []
    for index, label in enumerate(labels):
        if index < basis_count or not str(label).startswith("point:"):
            continue
        try:
            point_id = int(str(label).split(":", 1)[1])
            cross = [float(gram[i][index]) for i in range(basis_count)]
            coeffs = _solve_linear_system(basis_gram, cross)
            projection = sum(c * x for c, x in zip(cross, coeffs))
            height = float(gram[index][index])
            residual = height - projection
            scale = max(abs(height), 1e-30)
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
        out.append(
            {
                "point_id": point_id,
                "height": height,
                "orthogonal_height_residual": residual,
                "relative_residual": residual / scale,
                "basis_count": basis_count,
            }
        )
    return out
