"""缺陷定级阈值：全平台唯一的判定口径。

缺陷部位 × 缺陷等级 → 处置结论与处置时限（自发现日起算的自然日数）。
列表、详情、确认定级、批量定级、待办统计都只能调用 evaluate()，
不允许在各处各写一份判断，避免同一缺陷在列表和详情得出不同结论。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# 允许的缺陷等级（标准写法），顺序即严重程度
LEVELS = ["紧急", "重大", "一般"]

# 历史/别名写法归一化到标准等级
LEVEL_ALIASES = {
    "紧急": "紧急", "紧急缺陷": "紧急", "一级": "紧急", "1级": "紧急",
    "重大": "重大", "重大缺陷": "重大", "二级": "重大", "2级": "重大",
    "一般": "一般", "一般缺陷": "一般", "三级": "一般", "3级": "一般",
}

# 缺陷部位归类：按关键字命中，顺序即匹配优先级；都未命中归为「其他部位」
PART_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("叶片", ("叶片",)),
    ("齿轮箱", ("齿轮箱", "齿轮")),
    ("发电机", ("发电机",)),
    ("变桨系统", ("变桨", "桨距", "蓄电池")),
    ("偏航系统", ("偏航", "对风")),
]
DEFAULT_PART = "其他部位"

# 定级阈值：(部位分类, 等级) -> 自发现日起允许的最长消除天数
# 这张表是阈值的唯一存放处，调整口径只改这里。
LIMIT_DAYS: dict[tuple[str, str], int] = {
    ("叶片", "紧急"): 3,
    ("叶片", "重大"): 15,
    ("叶片", "一般"): 60,
    ("齿轮箱", "紧急"): 2,
    ("齿轮箱", "重大"): 10,
    ("齿轮箱", "一般"): 45,
    ("发电机", "紧急"): 2,
    ("发电机", "重大"): 10,
    ("发电机", "一般"): 45,
    ("变桨系统", "紧急"): 3,
    ("变桨系统", "重大"): 15,
    ("变桨系统", "一般"): 45,
    ("偏航系统", "紧急"): 7,
    ("偏航系统", "重大"): 20,
    ("偏航系统", "一般"): 60,
    (DEFAULT_PART, "紧急"): 7,
    (DEFAULT_PART, "重大"): 30,
    (DEFAULT_PART, "一般"): 90,
}

# 各等级对应的处置结论模板
CONCLUSION_TEMPLATES = {
    "紧急": "立即停机处置，{days}日内消除",
    "重大": "限期{days}日内消除，专人跟踪闭环",
    "一般": "纳入计划检修，{days}日内消除",
}


def parse_day(value: Any) -> date | None:
    """把 YYYY-MM-DD 文本解析成日期；缺失或格式不对一律返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def normalize_level(value: Any) -> str:
    """缺陷等级归一化；无法识别时返回空串，由卡控逻辑提示。"""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return LEVEL_ALIASES.get(text, LEVEL_ALIASES.get(text.replace("缺陷", ""), ""))


def classify_part(value: Any) -> str:
    """按缺陷部位文本归类到阈值表中的部位分类。"""
    text = str(value or "").strip()
    if not text:
        return DEFAULT_PART
    for category, keywords in PART_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return category
    return DEFAULT_PART


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def grading_blockers(entry: dict[str, Any], today: date | None = None) -> list[str]:
    """确认定级前的卡控项；返回空列表才允许保存定级结论。

    每一条都写清卡在哪个字段、期望什么口径，便于前端原样提示。
    """
    today = today or date.today()
    blockers: list[str] = []

    level = normalize_level(entry.get("缺陷等级"))
    if not level:
        blockers.append("缺陷等级缺失或不在允许范围（仅支持：紧急 / 重大 / 一般）")

    plan_day = parse_day(entry.get("计划消除日"))
    if plan_day is None:
        blockers.append("计划消除日缺失或日期格式不正确（应为 YYYY-MM-DD）")

    if level and plan_day is not None:
        part = classify_part(entry.get("缺陷部位"))
        days = LIMIT_DAYS[(part, level)]
        found_day = parse_day(entry.get("发现时间")) or today
        latest = found_day + timedelta(days=days)
        if plan_day > latest:
            blockers.append(
                f"计划消除日 {plan_day.isoformat()} 超出{part}-{level}定级阈值："
                f"自{found_day.isoformat()}起 {days} 天内消除，最迟不晚于 {latest.isoformat()}"
            )
    return blockers


def evaluate(entry: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """按唯一份阈值给一条缺陷下定判定结论。

    列表行、详情页、定级动作、批量任务、待办统计全部走这里，
    因此重新打开页面时各处拿到的结论必然相同。
    """
    today = today or date.today()
    part = classify_part(entry.get("缺陷部位"))
    level = normalize_level(entry.get("缺陷等级"))
    plan_day = parse_day(entry.get("计划消除日"))
    found_day = parse_day(entry.get("发现时间"))
    start_day = found_day or today

    days = LIMIT_DAYS.get((part, level)) if level else None
    latest = start_day + timedelta(days=days) if days else None
    threshold_ok = bool(level and plan_day is not None and latest and plan_day <= latest)

    if level:
        conclusion = CONCLUSION_TEMPLATES[level].format(days=days)
    else:
        conclusion = "等级未明，暂不能定级"

    blockers = grading_blockers(entry, today)
    overdue = (
        entry.get("status") == "处置中"
        and plan_day is not None
        and plan_day < today
    )

    return {
        "部位分类": part,
        "标准缺陷等级": level,
        "定级结论": conclusion,
        "处置时限天数": days,
        "起算日": _iso(start_day),
        "最迟消除日": _iso(latest),
        "计划消除日有效": plan_day is not None,
        "阈值达标": threshold_ok,
        "超期": overdue,
        "定级可确认": not blockers,
        "定级卡控项": blockers,
    }
