"""缺陷登记接口：维护机组缺陷，覆盖确认定级、提交消除、验收消除等动作。

定级阈值统一在后端 defect_rules 维护，列表与详情共用同一判定，
前端不再自行下结论。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.defect import defect_service as service

router = APIRouter(prefix="/api/defect", tags=["缺陷登记"])


@router.get("/summary")
def summary() -> dict[str, int]:
    """待办清单计数：按当前数据实时重算（待定级、处置中、超期、待办合计）。"""
    return service.summary()


@router.get("/overdue")
def list_overdue() -> dict[str, Any]:
    """超过计划消除日仍在处置中的缺陷，单独成清单。"""
    items = service.overdue_entries()
    return {"total": len(items), "items": items}


@router.post("/grading-jobs")
def start_grading_job() -> ActionResult:
    """发起批量定级：对全部待定级缺陷按编号逐条确认定级。"""
    job, message = service.start_grading_job()
    if job is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="批量定级任务已启动", entry=job)


@router.get("/grading-jobs/latest")
def latest_grading_job() -> dict[str, Any]:
    """重新打开页面时取回最近一份定级任务（含中断位置），用于接着走。"""
    job = service.latest_job()
    if job is None:
        return {"exists": False}
    return {"exists": True, **job}


@router.get("/grading-jobs/{job_id}")
def get_grading_job(job_id: int) -> dict[str, Any]:
    """读取单份批量定级任务进度；不存在时给可读说明。"""
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"定级任务 {job_id} 不存在")
    return job


@router.post("/grading-jobs/{job_id}/resume")
def resume_grading_job(job_id: int) -> ActionResult:
    """从中断点继续批量定级；断在第几条、卡在哪一项都随任务状态返回。"""
    job, message = service.resume_grading_job(job_id)
    if job is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message or "批量定级已继续", entry=job)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按缺陷编号检索"),
    status: str | None = Query(default=None, description="待定级、已定级、处置中、已消除"),
    overdue: bool = Query(default=False, description="只看超过计划消除日的处置中缺陷"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按缺陷编号与状态过滤缺陷登记列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, overdue_only=overdue, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出缺陷登记清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "defect", "total": total, "items": items}


@router.patch("/{entry_id}", response_model=ActionResult)
def patch_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """补录缺陷等级、计划消除日等字段，用于解除定级卡控项。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="缺陷信息已补录", entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条机组缺陷明细；不存在时给出可读的错误说明。

    明细中的判定与列表行的判定来自同一函数同一份阈值。
    """
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"机组缺陷 {entry_id} 不存在或已归档")
    return entry


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
