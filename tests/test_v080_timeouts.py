import time
import pytest
from rank42.timeouts import StageTimeout, hard_timeout


def test_hard_timeout_interrupts_single_candidate_stage():
    started=time.monotonic()
    with pytest.raises(StageTimeout):
        with hard_timeout(0.05,'demo'):
            time.sleep(1)
    assert time.monotonic()-started < 0.5
