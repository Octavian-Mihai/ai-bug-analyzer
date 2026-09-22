from buggy_pkg.calc import is_even


def test_is_even():
    assert is_even(4) is True
    assert is_even(5) is False
