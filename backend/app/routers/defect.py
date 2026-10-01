"""缺陷登记接口：维护机组缺陷，覆盖确认定级、提交消除、验收消除等动作。

定级阈值结论一律由 service 层给出，路由层不做业务判断；
超期清单、待办条数、批量定级断点续跑各自有独立入口。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.defect import DefectService

router = APIRouter(prefix="/api/defect", tags=["缺陷登记"])

service = DefectService()

LIST_FIELDS = ["缺陷编号", "缺陷部位", "缺陷等级", "发现方式", "发现时间", "报告人", "计划消除日", "缺陷状态"]
STATUSES = ["待定级", "已定级", "处置中", "已消除"]


class BatchGradeResult(BaseModel):
    """批量定级结果：本轮处理条数、断点位置与重算后的待办都在这里。"""

    ok: bool
    finished: bool
    message: str
    队列总数: int
    本次定级: int
    已定级编号: list[str]
    断点位置: int | None = None
    断点编号: str | None = None
    断点原因: str | None = None
    todo: dict


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按缺陷编号检索"),
    status: str | None = Query(default=None, description="待定级、已定级、处置中、已消除"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按缺陷编号与状态过滤缺陷登记列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/overdue")
def list_overdue() -> dict:
    """超期缺陷单独列出：处置中且计划消除日已过，按超期天数倒序。"""
    items = service.list_overdue()
    return {"module": "defect", "total": len(items), "items": items}


@router.get("/todo")
def todo_summary() -> dict:
    """待办清单条数：每次按当前数据重算，并给出上轮批量定级断点。"""
    return service.todo_summary()


@router.get("/export")
def export_entries() -> dict:
    """导出缺陷登记清单：返回当前过滤条件下的全量数据（含统一定级判定）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "defect", "total": total, "items": items}


@router.post("/batch-grade", response_model=BatchGradeResult)
def grade_pending_batch() -> BatchGradeResult:
    """批量定级：顺序处理待定级缺陷，遇缺项停在该条；再次调用从断点接着走。"""
    result = service.grade_pending_batch()
    return BatchGradeResult(**result)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条机组缺陷明细（含与列表完全相同的定级判定）；不存在时给出可读说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"机组缺陷 {entry_id} 不存在或已归档")
    return entry


@router.patch("/{entry_id}", response_model=ActionResult)
def patch_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """补录缺陷等级、计划消除日等字段；缺项未补齐时明确说明还卡在哪一项。"""
    entry, message = service.patch_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    still_blocked = bool(entry.get("定级缺项"))
    return ActionResult(ok=not still_blocked, message=message, entry=entry)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条机组缺陷，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="机组缺陷已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条机组缺陷执行确认定级、提交消除、验收消除；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
