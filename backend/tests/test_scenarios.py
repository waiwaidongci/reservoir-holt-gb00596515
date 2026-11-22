"""情景触线概率区间的领域口径测试（仅依赖标准库）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.domain import scenarios  # noqa: E402

LEDGER = [
    {"reservoir_id": "RW-02", "reading_date": "2026-11-19", "level_m": 1.83},
    {"reservoir_id": "RW-02", "reading_date": "2026-11-20", "level_m": 1.91},
]
PARAMS = {"alert_level_m": 2.05, "horizon_days": 3}


def entry(scenario, day, rise, weight=0.5, revision=1):
    return {"scenario": scenario, "forecast_date": day, "net_rise_m": rise,
            "weight": weight, "revision": revision}


def test_complete_scenarios_determine_lower_bound():
    entries = [
        entry("偏多", "2026-11-21", 0.10, 0.3), entry("偏多", "2026-11-22", 0.05, 0.3),
        entry("偏多", "2026-11-23", 0.02, 0.3),
        entry("偏少", "2026-11-21", -0.05, 0.7), entry("偏少", "2026-11-22", -0.05, 0.7),
        entry("偏少", "2026-11-23", -0.05, 0.7),
    ]
    result = scenarios.compute(LEDGER, entries, PARAMS)
    # 偏多：1.91+0.10+0.05=2.06 于 11-22 触线；偏少不触线。
    assert result["interval"] == {"lower": 0.3, "upper": 0.3}
    by_name = {item["scenario"]: item for item in result["scenarios"]}
    assert by_name["偏多"]["first_touch"] == "2026-11-22"
    assert by_name["偏多"]["counts_toward"] == "lower"
    assert by_name["偏少"]["counts_toward"] == "none"
    assert result["window"] == {"start": "2026-11-21", "end": "2026-11-23", "days": 3}
    assert result["base"] == {"date": "2026-11-20", "level_m": 1.91}


def test_missing_days_only_count_toward_upper_bound():
    entries = [
        entry("完整", "2026-11-21", 0.0, 0.5), entry("完整", "2026-11-22", 0.0, 0.5),
        entry("完整", "2026-11-23", 0.0, 0.5),
        # 缺一天，即使已报日期已经触线，也只计入概率上界。
        entry("缺天", "2026-11-21", 0.20, 0.5), entry("缺天", "2026-11-23", 0.0, 0.5),
    ]
    result = scenarios.compute(LEDGER, entries, PARAMS)
    assert result["interval"] == {"lower": 0.0, "upper": 0.5}
    by_name = {item["scenario"]: item for item in result["scenarios"]}
    assert by_name["缺天"]["touches"] is True
    assert by_name["缺天"]["counts_toward"] == "upper"
    assert by_name["缺天"]["reported_days"] == 2


def test_weights_are_used_as_declared_without_renormalization():
    entries = [
        entry("甲", "2026-11-21", 0.2, 0.2), entry("甲", "2026-11-22", 0.0, 0.2),
        entry("甲", "2026-11-23", 0.0, 0.2),
        entry("乙", "2026-11-21", 0.0, 0.3), entry("乙", "2026-11-22", 0.0, 0.3),
        entry("乙", "2026-11-23", 0.0, 0.3),
    ]
    result = scenarios.compute(LEDGER, entries, PARAMS)
    # 权重合计 0.5 不归一化：下界就是 0.2。
    assert result["interval"] == {"lower": 0.2, "upper": 0.2}


def test_weight_sum_above_one_is_rejected():
    entries = [entry("甲", "2026-11-21", 0.0, 0.6), entry("乙", "2026-11-21", 0.0, 0.6)]
    try:
        scenarios.compute(LEDGER, entries, PARAMS)
    except ValueError as error:
        assert "权重合计" in str(error)
    else:
        raise AssertionError("权重合计超过 1 应当判为无效")


def test_base_level_already_at_line_reports_full_probability():
    ledger = [dict(LEDGER[0]), {"reservoir_id": "RW-02", "reading_date": "2026-11-20", "level_m": 2.06}]
    entries = [entry("甲", "2026-11-21", -0.5, 1.0), entry("甲", "2026-11-22", 0.0, 1.0),
               entry("甲", "2026-11-23", 0.0, 1.0)]
    result = scenarios.compute(ledger, entries, PARAMS)
    assert result["interval"] == {"lower": 1.0, "upper": 1.0}
    assert "已达预警线" in result["headline"]


def test_latest_revision_wins_within_a_part():
    rows = [
        {"scenario": "偏多", "weight": 0.5, "forecast_date": "2026-11-21", "net_rise_m": 0.01, "revision": 1},
        {"scenario": "偏多", "weight": 0.5, "forecast_date": "2026-11-21", "net_rise_m": 0.09, "revision": 3},
        {"scenario": "偏多", "weight": 0.5, "forecast_date": "2026-11-21", "net_rise_m": 0.05, "revision": 2},
    ]
    merged = scenarios.latest_revision_rows(rows)
    assert len(merged) == 1 and merged[0]["net_rise_m"] == 0.09 and merged[0]["revision"] == 3


def test_same_revision_duplicate_in_one_part_is_rejected():
    rows = [
        {"scenario": "偏多", "weight": 0.5, "forecast_date": "2026-11-21", "net_rise_m": 0.01, "revision": 2},
        {"scenario": "偏多", "weight": 0.5, "forecast_date": "2026-11-21", "net_rise_m": 0.02, "revision": 2},
    ]
    try:
        scenarios.latest_revision_rows(rows)
    except ValueError as error:
        assert "修订号" in str(error)
    else:
        raise AssertionError("同号重复修订应当判为报文有误")


def test_parse_part_csv_validates_and_merges():
    text = ("scenario,weight,forecast_date,net_rise_m,revision\n"
            "偏多,0.5,2026-11-21,0.01,1\n"
            "偏多,0.5,2026-11-21,0.04,2\n")
    rows = scenarios.parse_part_csv(text)
    assert rows == [{"scenario": "偏多", "weight": 0.5, "forecast_date": "2026-11-21",
                     "net_rise_m": 0.04, "revision": 2}]


def test_parse_part_csv_rejects_bad_header():
    try:
        scenarios.parse_part_csv("scenario,weight\n偏多,0.5\n")
    except ValueError as error:
        assert "表头" in str(error)
    else:
        raise AssertionError("缺列的报文应当被拒绝")


def test_empty_version_has_no_entries():
    try:
        scenarios.compute(LEDGER, [], PARAMS)
    except ValueError as error:
        assert "情景条目" in str(error)
    else:
        raise AssertionError("空版次应当判为不可计算")


if __name__ == "__main__":
    checks = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for check in checks:
        check()
        print(f"ok {check.__name__}")
    print(f"{len(checks)} checks passed")
