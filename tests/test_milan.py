from ai_bot.astro import ashtakoot, moon_of, _NADI_OF, _GANA_OF, _ANIMAL, _YONI


def test_self_match_is_28_nadi_zero():
    m = moon_of("1995-05-17", "15:30", {"tz": "Asia/Kolkata"})
    r = ashtakoot(m, m)
    assert r["total"] == 28 and r["parts"]["Nadi"][0] == 0 and r["parts"]["Tara"][0] == 3


def test_tables_sane():
    assert sorted(_NADI_OF.count(i) for i in range(3)) == [9, 9, 9]
    assert sorted(_GANA_OF.count(i) for i in range(3)) == [9, 9, 9]
    assert len(_ANIMAL) == 27 and all(_YONI[i][i] == 4 for i in range(14))
    assert all(_YONI[i][j] == _YONI[j][i] for i in range(14) for j in range(14))
    assert _YONI[0][8] == 0 and _YONI[1][13] == 0  # horse-buffalo, elephant-lion are enemies


def test_total_in_range_and_dosha():
    a = moon_of("1995-05-17", "15:30", {"tz": "Asia/Kolkata"}); b = moon_of("1996-11-02", "08:10", {"tz": "Asia/Kolkata"})
    r = ashtakoot(a, b)
    assert 0 <= r["total"] <= 36 and r["parts"]["Nadi"][0] in (0, 8)
