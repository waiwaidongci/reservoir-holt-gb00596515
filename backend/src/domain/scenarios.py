"""雨量站情景预报的触线概率区间计算。

雨量站按版次分批送情景预报，同一版次内乱序补报按情景和日期取最新修订。
交班口径：从同一晨检液位出发，逐日累加各情景的净涨幅，按本版原定权重汇总
未来几天至少一次触线的概率；缺天的情景只计入概率上界。
"""

import csv
import io
import math
from datetime import date, timedelta
from math import isclose

_DAY = timedelta(days=1)
_ALERT_TOLERANCE_M = 1e-12
_MAX_NET_RISE_M = 5.0
_MAX_PART_ROWS = 5000

SCENARIO_PARAMS = [
    {"key": "alert_level_m", "label": "高位预警线（m）", "type": "number", "required": True,
     "min": 0.01, "max": 20, "default": 2.05, "help": "累计液位达到此值即计为触线。"},
    {"key": "horizon_days", "label": "未来预报天数", "type": "int", "required": True,
     "min": 1, "max": 14, "default": 6, "help": "从最新晨检日期的次日开始逐日累加。"},
]

PART_FIELDS = [
    {"key": "scenario", "label": "情景编号", "type": "string", "required": True, "max_length": 40},
    {"key": "weight", "label": "原定权重", "type": "number", "required": True, "min": 0, "max": 1},
    {"key": "forecast_date", "label": "预报日期", "type": "date", "required": True},
    {"key": "net_rise_m", "label": "当日净涨幅（m）", "type": "number", "required": True,
     "min": -_MAX_NET_RISE_M, "max": _MAX_NET_RISE_M},
    {"key": "revision", "label": "修订号", "type": "int", "required": True, "min": 1},
]


def _cell(field, raw, line):
    label = field["label"]
    value = raw.strip() if isinstance(raw, str) else raw
    if value is None or value == "":
        raise ValueError(f"CSV 第 {line} 行 · {label}不能为空")
    try:
        if field["type"] == "int":
            if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                raise ValueError
            value = int(value)
        elif field["type"] == "number":
            if isinstance(value, bool):
                raise ValueError
            value = float(value)
            if not math.isfinite(value):
                raise ValueError
        elif field["type"] == "date":
            value = date.fromisoformat(str(value)).isoformat()
        elif not isinstance(value, str) or len(value) > field.get("max_length", 300):
            raise ValueError
    except (TypeError, ValueError, KeyError, OverflowError):
        raise ValueError(f"CSV 第 {line} 行 · {label}格式不正确") from None
    if field["type"] in {"int", "number"}:
        if "min" in field and value < field["min"]:
            raise ValueError(f"CSV 第 {line} 行 · {label}最小为 {field['min']}")
        if "max" in field and value > field["max"]:
            raise ValueError(f"CSV 第 {line} 行 · {label}最大为 {field['max']}")
    return value


def latest_revision_rows(rows):
    """同一批报文内按情景和日期取最新修订；同号重复视为报文有误。"""
    merged = {}
    for row in rows:
        key = (row["scenario"], row["forecast_date"])
        current = merged.get(key)
        if current is None or row["revision"] > current["revision"]:
            merged[key] = row
        elif row["revision"] == current["revision"]:
            raise ValueError(f"情景 {key[0]} 在 {key[1]} 有两条修订号 {row['revision']} 的记录，请核对后重新报送。")
    return list(merged.values())


def parse_part_csv(text):
    """解析一个版次的一批情景预报 CSV，返回按最新修订去重后的条目。"""
    reader = csv.DictReader(io.StringIO(text.removeprefix('\ufeff'), newline=""), strict=True)
    keys = {field["key"] for field in PART_FIELDS}
    try:
        headers = reader.fieldnames or []
        if set(headers) != keys or len(headers) != len(set(headers)):
            raise ValueError("CSV 表头与情景预报字段不符，请下载模板检查列名")
        rows = []
        for raw in reader:
            if len(rows) >= _MAX_PART_ROWS:
                raise ValueError(f"一批报文最多导入 {_MAX_PART_ROWS} 行")
            try:
                if None in raw or any(value is None for value in raw.values()):
                    raise ValueError("工作表的列数与表头不一致")
                rows.append({field["key"]: _cell(field, raw.get(field["key"]), reader.line_num) for field in PART_FIELDS})
            except ValueError as error:
                message = str(error)
                raise ValueError(message if message.startswith("CSV") else f"CSV 第 {reader.line_num} 行 · {message}") from None
    except csv.Error:
        raise ValueError(f"CSV 第 {reader.line_num} 行附近格式不正确") from None
    if not rows:
        raise ValueError("CSV 至少需要一行有效数据")
    return latest_revision_rows(rows)


def normalize_params(params):
    fields = {field["key"]: field for field in SCENARIO_PARAMS}
    if set(params) - fields.keys():
        raise ValueError("存在未知情景计算参数，请重新打开交班页面。")
    values = {}
    for key, field in fields.items():
        value = params.get(key, field["default"])
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not field["min"] <= value <= field["max"]
                or (field["type"] == "int" and not isinstance(value, int))):
            raise ValueError(f'{field["label"]}必须为 {field["min"]}–{field["max"]} 范围内的有效值。')
        values[key] = value
    return values


def _pct(value):
    return f"{f'{value * 100:.1f}'.rstrip('0').rstrip('.')}%"


def _reaches(level, threshold):
    return level >= threshold or isclose(level, threshold, rel_tol=0, abs_tol=_ALERT_TOLERANCE_M)


def _table(title, columns, rows):
    return {"title": title, "columns": [{"key": key, "label": label} for key, label in columns], "rows": rows}


def compute(readings_rows, entries, params):
    """从同一晨检液位逐日累加各情景净涨幅，输出至少一次触线的概率区间。

    readings_rows 为晨检液位台账行，entries 为本版次按（情景, 日期）取最新修订后的
    情景条目，params 为 normalize_params 归一化后的参数。
    """
    from .analysis import validate_rows

    options = normalize_params(params)
    threshold = options["alert_level_m"]
    horizon = options["horizon_days"]
    reservoir_id, readings = validate_rows(readings_rows)
    base_date, base_level = readings[-1]
    if (date.max - base_date).days < horizon:
        raise ValueError("预报日期超出日历范围，请缩短未来预报天数。")
    window = [base_date + step * _DAY for step in range(1, horizon + 1)]
    if not entries:
        raise ValueError("该版次还没有可用的情景条目，请先接收情景预报。")

    grouped = {}
    for order, entry in enumerate(entries):
        try:
            scenario = str(entry["scenario"]).strip()
            day = entry["forecast_date"]
            day = day if isinstance(day, date) else date.fromisoformat(str(day))
            net_rise = float(entry["net_rise_m"])
            weight = float(entry["weight"])
            revision = int(entry["revision"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"第 {order + 1} 条情景条目无效，请检查情景编号、日期、净涨幅和权重。") from None
        if not scenario or not 0 <= weight <= 1 or not -_MAX_NET_RISE_M <= net_rise <= _MAX_NET_RISE_M or revision < 1:
            raise ValueError(f"第 {order + 1} 条情景条目无效，请检查情景编号、日期、净涨幅和权重。")
        slot = grouped.setdefault(scenario, {"days": {}, "weight": weight, "weight_revision": -1})
        slot["days"][day] = net_rise
        if revision >= slot["weight_revision"]:
            slot["weight"], slot["weight_revision"] = weight, revision
    if not grouped:
        raise ValueError("该版次还没有可用的情景条目，请先接收情景预报。")
    total_weight = sum(slot["weight"] for slot in grouped.values())
    if total_weight > 1 + 1e-9:
        raise ValueError(f"本版情景原定权重合计 {round(total_weight, 6)} 超过 1，请核对上游报文。")

    scenarios = []
    daily = []
    for scenario in sorted(grouped, key=lambda name: (-grouped[name]["weight"], name)):
        slot = grouped[scenario]
        reported = [day for day in window if day in slot["days"]]
        complete = len(reported) == horizon
        level = base_level
        first_touch = None
        for day in reported:
            level += slot["days"][day]
            touched = _reaches(level, threshold)
            if touched and first_touch is None:
                first_touch = day
            daily.append({"scenario": scenario, "date": day.isoformat(), "net_rise_m": slot["days"][day],
                          "level_m": round(level, 6), "reaches_alert": touched})
        if complete:
            contribution = "lower" if first_touch else "none"
        else:
            # 缺天的情景只计入概率上界，即使已报日期已经触线。
            contribution = "upper"
        scenarios.append({
            "scenario": scenario, "weight": round(slot["weight"], 6),
            "reported_days": len(reported), "required_days": horizon, "complete": complete,
            "touches": bool(first_touch), "first_touch": first_touch.isoformat() if first_touch else None,
            "final_level_m": round(level, 6), "counts_toward": contribution,
        })

    already = _reaches(base_level, threshold)
    lower = round(sum(item["weight"] for item in scenarios if item["counts_toward"] == "lower"), 6)
    upper = round(lower + sum(item["weight"] for item in scenarios if item["counts_toward"] == "upper"), 6)
    if already:
        lower = upper = 1.0
        headline = f"晨检液位 {base_level} m 已达预警线，未来 {horizon} 天触线概率按 100% 计"
    elif lower == upper:
        headline = f"未来 {horizon} 天至少一次触线概率 {_pct(lower)}"
    else:
        headline = f"未来 {horizon} 天至少一次触线概率 {_pct(lower)}–{_pct(upper)}"
    start, end = window[0].isoformat(), window[-1].isoformat()
    missing = sum(1 for item in scenarios if not item["complete"])
    note = (f"{reservoir_id} 以 {base_date.isoformat()} 晨检液位 {base_level} m 为共同基准，"
            f"按本版 {len(scenarios)} 个情景的原定权重逐日累加净涨幅；"
            f"{missing} 个缺天情景仅计入概率上界。")
    contribution_label = {"lower": "计入下界", "upper": "仅计入上界", "none": "未触线"}
    tables = [
        _table("情景触线明细",
               (("scenario", "情景"), ("weight", "原定权重"), ("reported_days", "已报天数"),
                ("required_days", "窗口天数"), ("final_level_m", "窗口末日累计（m）"),
                ("first_touch", "首次触线"), ("contribution", "计入区间")),
               [{**item, "contribution": contribution_label[item["counts_toward"]],
                 "first_touch": item["first_touch"] or "—"} for item in scenarios]),
        _table("逐日累计液位",
               (("scenario", "情景"), ("date", "预报日期"), ("net_rise_m", "当日净涨幅（m）"),
                ("level_m", "累计液位（m）"), ("reaches_alert", "达到预警线")), daily),
    ]
    return {
        "state": "warning" if upper > 0 else "ok",
        "headline": headline,
        "note": note,
        "metrics": [
            {"label": "触线概率下界", "value": _pct(lower), "detail": "完整且触线情景的权重合计"},
            {"label": "触线概率上界", "value": _pct(upper), "detail": "下界加缺天情景权重"},
            {"label": "基准晨检液位（m）", "value": base_level, "detail": f"{base_date.isoformat()} 晨检"},
            {"label": "预警线（m）", "value": threshold, "detail": "累计液位达到即触线"},
            {"label": "预报窗口", "value": f"{start} 至 {end}", "detail": f"未来 {horizon} 天"},
            {"label": "情景数", "value": len(scenarios), "detail": f"其中 {missing} 个缺天"},
        ],
        "tables": tables,
        "charts": [
            {"title": "各情景窗口末日累计液位（m）", "kind": "bar",
             "series": [{"label": item["scenario"], "value": item["final_level_m"]} for item in scenarios]},
        ],
        "sections": [
            {"title": "权重口径", "text": "各情景按本版原定权重计入，不因缺天或缺报重新归一化；权重合计超过 1 时本次计算判为无效。"},
            {"title": "缺天处理", "text": "窗口内缺天的情景只计入概率上界，不计入下界；其已报日期的累计液位仍在明细表中列出。"},
            {"title": "修订口径", "text": "同一版次内乱序补报按情景和日期取最新修订；上游发布新版次后，在算的旧版结果失效，已发布结果在新版算好前仍可查询。"},
        ],
        "interval": {"lower": lower, "upper": upper},
        "window": {"start": start, "end": end, "days": horizon},
        "base": {"date": base_date.isoformat(), "level_m": base_level},
        "alert_level_m": threshold,
        "scenarios": scenarios,
    }
