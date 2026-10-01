"""缺陷定级阈值与判定口径——全平台只允许这一份。

列表接口、详情接口、批量定级都必须调用 :func:`grade_view` / :func:`assess`，
任何地方都不允许再按缺陷部位、缺陷等级另写一套时限，否则两处结论会打架。

判定分两类输出：
- ``定级判定``：定级时限是否达标（按缺陷部位分组取阈值，按缺陷等级取默认时限）；
- ``是否超期``：处置中缺陷的计划消除日是否早于今天（已消除的缺陷不再算超期）。
"""
from __future__ import annotations

from datetime import date
from typing import Any

# 部位分组：键是判定口径里对外暴露的分组名，值是用于匹配“缺陷部位”的关键词。
# 匹配方式为包含匹配（如部位“1号变桨电池组”会命中“变桨”→ 旋转部件）。
PART_GROUPS: dict[str, tuple[str, ...]] = {
    "旋转部件": ("齿轮箱", "发电机", "主轴", "轴承", "叶轮", "叶片", "变桨"),
    "承载结构": ("塔筒", "塔架", "基础", "法兰", "螺栓", "机舱"),
    "电气系统": ("电缆", "集电线路", "箱变", "开关柜", "电气", "变流器"),
    "其他": (),
}

# 定级时限（天）：先按部位分组取，分组没有配置的等级再落到等级默认时限。
# 阈值只允许在这里维护，不要散落到路由或前端。
GRADE_LIMITS: dict[str, dict[str, int]] = {
    "旋转部件": {"紧急": 1, "重大": 3, "一般": 7, "轻微": 15},
    "承载结构": {"紧急": 2, "重大": 5, "一般": 10, "轻微": 20},
    "电气系统": {"紧急": 1, "重大": 4, "一般": 8, "轻微": 15},
    "其他": {"紧急": 3, "重大": 7, "一般": 15, "轻微": 30},
}

# 缺陷等级的默认时限：部位分组未单列该等级时使用。
DEFAULT_LIMITS: dict[str, int] = {"紧急": 3, "重大": 7, "一般": 15, "轻微": 30}

VALID_GRADES = tuple(DEFAULT_LIMITS.keys())
GRADE_FIELDS = ("缺陷等级", "计划消除日")  # 确认定级必须齐备的两项


def part_group(part: str) -> str:
    """按缺陷部位关键词归入唯一分组；都不命中归“其他”。"""
    for group, keywords in PART_GROUPS.items():
        if any(keyword in part for keyword in keywords):
            return group
    return "其他"


def grade_limit(group: str, grade: str) -> int:
    """取某部位分组下某等级的定级时限（天）。"""
    return GRADE_LIMITS.get(group, GRADE_LIMITS["其他"]).get(
        grade, DEFAULT_LIMITS.get(grade, DEFAULT_LIMITS["轻微"])
    )


def parse_day(value: Any) -> date | None:
    """解析 YYYY-MM-DD 日期；空值或非法格式一律返回 None，由调用方决定怎么提示。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def missing_grade_fields(row: dict[str, Any]) -> list[str]:
    """确认定级前的卡口：缺陷等级、计划消除日缺哪项就报哪项。"""
    blocked: list[str] = []
    for field in GRADE_FIELDS:
        if not str(row.get(field) or "").strip():
            blocked.append(field)
        elif field == "计划消除日" and parse_day(row.get(field)) is None:
            blocked.append("计划消除日（需为 YYYY-MM-DD 日期）")
    grade = str(row.get("缺陷等级") or "").strip()
    if grade and grade not in VALID_GRADES:
        blocked.append(f"缺陷等级（仅支持 {'、'.join(VALID_GRADES)}）")
    return blocked


def assess(row: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """对一条缺陷做唯一一次判定，返回结构化结论。

    返回键：``定级判定`` / ``判定说明`` / ``判定部位分组`` / ``定级时限天数`` /
    ``剩余天数`` / ``是否超期`` / ``超期天数`` / ``定级缺项``。
    列表与详情共用本函数，保证两处结论永远一致。
    """
    today = today or date.today()
    part = str(row.get("缺陷部位") or "").strip() or "未填写部位"
    group = part_group(part)
    grade = str(row.get("缺陷等级") or "").strip()
    plan_day = parse_day(row.get("计划消除日"))

    missing = missing_grade_fields(row)
    result: dict[str, Any] = {
        "定级判定": "无法判定",
        "判定说明": "缺少定级依据：" + "、".join(missing),
        "判定部位分组": group,
        "定级时限天数": None,
        "剩余天数": None,
        "是否超期": False,
        "超期天数": 0,
        "定级缺项": missing,
    }
    if missing:
        return result

    limit = grade_limit(group, grade)
    result["定级时限天数"] = limit
    remaining = (plan_day - today).days
    result["剩余天数"] = remaining
    if remaining < 0:
        result["定级判定"] = "超阈值"
        result["判定说明"] = (
            f"{group}·{grade}缺陷限{limit}天内定级，计划消除日{plan_day.isoformat()}已超过判定日{today.isoformat()}"
            f"（超{-remaining}天）"
        )
    else:
        result["定级判定"] = "阈值内"
        result["判定说明"] = (
            f"{group}·{grade}缺陷限{limit}天内定级，计划消除日{plan_day.isoformat()}，剩余{remaining}天"
        )

    # 超期只针对“处置中”缺陷：已消除不再追踪，待定级/已定级尚未进入处置阶段。
    if str(row.get("status") or "") == "处置中":
        overdue_days = (today - plan_day).days
        if overdue_days > 0:
            result["是否超期"] = True
            result["超期天数"] = overdue_days
    return result


def grade_view(row: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """返回可直接进列表/详情响应的判定字段（平铺，供前端直接展示）。"""
    conclusion = assess(row, today=today)
    return {
        "定级判定": conclusion["定级判定"],
        "判定说明": conclusion["判定说明"],
        "判定部位分组": conclusion["判定部位分组"],
        "定级时限天数": conclusion["定级时限天数"],
        "剩余天数": conclusion["剩余天数"],
        "是否超期": conclusion["是否超期"],
        "超期天数": conclusion["超期天数"],
        "定级缺项": conclusion["定级缺项"],
    }
