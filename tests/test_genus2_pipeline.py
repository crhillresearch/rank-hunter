"""Mathematical positive and negative controls for the genus-two workbench."""
from __future__ import annotations

import unittest

from rank42.genus2_pipeline import (
    audit_model,
    quadratic_divisor_rank_search,
)
from rank42.pipeline_catalog import template_stages, validate_pipeline


JAC_C001 = (72256, 79296, -1872, -22560, -3540, 1500, 361)


class GenusTwoPipelineTests(unittest.TestCase):
    def test_generic_search_reproduces_published_rank_four_control(self):
        result = quadratic_divisor_rank_search(JAC_C001)
        self.assertEqual(result["proof_status"], "rigorous_rank_lower_bound")
        self.assertEqual(result["rank_lower_bound"], 4)
        self.assertEqual(result["fake_descent_image_dimension"], 3)
        self.assertEqual(result["fps_kernel_direction"], 1)
        self.assertEqual(result["rational_2_torsion_dimension_upper"], 0)
        self.assertEqual(
            [(row["prime"], row["jacobian_order_Fp"])
             for row in result["local_jacobian_orders"] if row["prime"] in {5, 11, 29}],
            [(5, 70), (11, 144), (29, 855)],
        )
        self.assertTrue(result["search_complete"])
        self.assertTrue(result["modular_character_rows"])

    def test_entered_divisor_is_checked_exactly(self):
        with self.assertRaisesRegex(ValueError, "does not divide"):
            quadratic_divisor_rank_search(
                JAC_C001,
                numerator_bound=1,
                denominator_bound=1,
                seed_divisors=[{"q": [-8, 0, 1], "v": [1, 1]}],
            )

    def test_quintic_divisors_do_not_claim_sextic_rank_proof(self):
        result = quadratic_divisor_rank_search(
            [1, 1, 0, 0, 0, 1],
            numerator_bound=2,
            denominator_bound=1,
        )
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result["rank_lower_bound"])
        self.assertEqual(result["proof_status"], "no_rank_certificate")

    def test_invalid_model_and_pipeline_contract(self):
        self.assertEqual(audit_model([0, 0, 0, 0, 0, 0, 1])["status"], "invalid")
        stages = template_stages("genus2_research", "genus2_jacobian")
        self.assertFalse(validate_pipeline("genus2_jacobian", stages))
        self.assertIn("genus2_divisor_rank_search", [stage["id"] for stage in stages])


if __name__ == "__main__":
    unittest.main()
