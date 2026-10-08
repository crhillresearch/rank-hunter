from rank42.hole_jobs import make_hole_job, validate_hole_job


def test_hole_job_schema():
    d = make_hole_job(
        coefficients=[1, 2, 3, 4, 5],
        height=1000,
        hole_label="half-lattice:00101",
        curve_id=7,
        metadata={"basis_rank": 17},
    )
    assert validate_hole_job(d) is d
    assert d["schema"] == "rank42.hole-quartic-job.v1"
    assert d["curve_id"] == 7
