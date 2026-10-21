from rank42.curve_inventory import log_conductor


def test_log_conductor_is_natural_log_and_safe():
    import math
    assert abs(log_conductor({'conductor': '1000'}) - math.log(1000)) < 1e-12
    assert log_conductor({'conductor': None}) is None
    assert log_conductor({'conductor': 'bad'}) is None
