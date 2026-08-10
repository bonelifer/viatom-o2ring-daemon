from viatom_o2ring_daemon.categories import MILD, MODERATE, NORMAL, SEVERE, classify


def test_classify_normal():
    assert classify(98) == NORMAL
    assert classify(95) == NORMAL


def test_classify_mild():
    assert classify(94) == MILD
    assert classify(90) == MILD


def test_classify_moderate():
    assert classify(89) == MODERATE
    assert classify(85) == MODERATE


def test_classify_severe():
    assert classify(84) == SEVERE
    assert classify(0) == SEVERE


def test_classify_none():
    assert classify(None) is None
