"""Exact point/model conversion helpers shared by core and Family workflows."""
from __future__ import annotations


def map_points_exact(source_curve, target_curve, points):
    """Map points through the exact Sage isomorphism and verify target membership."""
    iso = source_curve.isomorphism_to(target_curve)
    mapped = []
    for index, point in enumerate(points):
        image = iso(point)
        if image.is_zero():
            mapped.append(image)
            continue
        # Reconstructing on the target is an exact equation-membership check.
        try:
            checked = target_curve(image[0], image[1])
        except Exception as exc:
            raise ValueError(f"mapped point #{index} is not on target model: {exc}") from exc
        mapped.append(checked)
    return mapped
