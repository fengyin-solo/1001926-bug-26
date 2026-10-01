"""缺陷登记业务规则：定级阈值口径、状态流转、字段校验、超期追踪与批量定级断点续跑。

阈值本身只在 ``app.services.grading`` 维护一份；本模块只负责取数、落库与流转。
列表与详情都经过 :meth:`_serialize` 输出，结论随数据实时重算，刷新页面后两处一致。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.grading import grade_view, missing_grade_fields
from app.store import store

MODULE = "defect"
REQUIRED_FIELDS = ["缺陷编号", "缺陷部位", "缺陷等级"]
STATUS_ORDER = ["待定级", "已定级", "处置中", "已消除"]
ACTION_RULES = {"确认定级": "已定级", "提交消除": "处置中", "验收消除": "已消除"}
NEGATIVE_ACTIONS = []


class DefectService:
    def __init__(self) -> None:
        # 最近一次批量定级的断点信息；只在内存里保留最近一轮，用于“接着走”。
        self._batch_state: dict[str, Any] = {}

    # ---------- 读取：列表与详情走同一个序列化口径 ----------

    def _serialize(self, row: dict[str, Any]) -> dict[str, Any]:
        """给存储行补上同一份实时判定；不改存储行，保证每次请求都重算。"""
        view = dict(row)
        view.update(grade_view(row))
        # 定级结论只认当前这份阈值：曾经落库的旧结论（阈值调整前）不对外暴露，
        # 真正覆盖落库发生在再次执行确认定级时（_apply_grade）。
        if row.get("定级时间"):
            view["定级结论"] = view["定级判定"]
            view["定级依据"] = view["判定说明"]
        else:
            view.pop("定级结论", None)
            view.pop("定级依据", None)
        # 异常量以“处置中超期”为准，待办量以“待定级”为准，供运营概览汇总。
        view["abnormal"] = bool(view["是否超期"])
        view["pending"] = row.get("status") == STATUS_ORDER[0]
        return view

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("缺陷编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = rows[start:start + size]
        return [self._serialize(row) for row in page_rows], total

    def list_overdue(self) -> list[dict[str, Any]]:
        """超期缺陷单独列出：处置中且计划消除日已过，按超期天数倒序。"""
        rows = [self._serialize(row) for row in store.rows(MODULE) if row.get("status") == "处置中"]
        rows = [row for row in rows if row["是否超期"]]
        rows.sort(key=lambda row: row["超期天数"], reverse=True)
        return rows

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._serialize(row) if row is not None else None

    def todo_summary(self) -> dict[str, Any]:
        """待办清单条数：每次调用都按当前数据重算，不缓存旧数。"""
        rows = store.rows(MODULE)
        pending_rows = [row for row in rows if row.get("status") == STATUS_ORDER[0]]
        blocked = [row for row in pending_rows if missing_grade_fields(row)]
        overdue = [
            row for row in rows
            if row.get("status") == "处置中" and grade_view(row)["是否超期"]
        ]
        state = self._batch_state
        return {
            "待定级总数": len(pending_rows),
            "可立即定级": len(pending_rows) - len(blocked),
            "缺项卡住": len(blocked),
            "处置中超期": len(overdue),
            "断点编号": state.get("stopped_id"),
            "断点位置": state.get("stopped_position"),
            "断点原因": state.get("stopped_reason"),
        }

    # ---------- 写入 ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ("发现方式", "发现时间", "报告人", "计划消除日"):
            if str(values.get(field) or "").strip():
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._serialize(entry), []

    def _apply_grade(self, row: dict[str, Any]) -> dict[str, Any]:
        """落定一条缺陷的等级结论；重复定级只保留最新一次。"""
        view = self._serialize(row)
        stamp = datetime.now().replace(microsecond=0).isoformat()
        # 每次定级整段覆盖，旧结论不保留，避免列表/详情读到历史口径。
        row["定级结论"] = view["定级判定"]
        row["定级依据"] = view["判定说明"]
        row["定级时限天数"] = view["定级时限天数"]
        row["定级时间"] = stamp
        row["定级轮次"] = int(row.get("定级轮次", 0)) + 1
        row["status"] = STATUS_ORDER[1]
        row["pending"] = False
        return row

    def confirm_grade(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """确认定级：缺项不放行，说明卡在哪一项；通过则覆盖为最新定级结论。"""
        row = store.find(MODULE, entry_id)
        if row is None:
            return None, f"机组缺陷 {entry_id} 不存在或已归档"
        if row.get("status") != STATUS_ORDER[0]:
            return None, f"缺陷{row.get('缺陷编号', entry_id)}当前状态为{row.get('status')}，只有待定级缺陷可以确认定级"
        blocked = missing_grade_fields(row)
        if blocked:
            return None, f"缺陷{row.get('缺陷编号', entry_id)}无法定级，卡在：{'、'.join(blocked)}"
        row = self._apply_grade(row)
        return self._serialize(row), f"缺陷{row['缺陷编号']}已确认定级（第{row['定级轮次']}次定级，仅保留最新结论）"

    def patch_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """补录/修正缺陷字段（主要用于补齐定级缺项）；已定级后的核心字段不允许再改。"""
        row = store.find(MODULE, entry_id)
        if row is None:
            return None, f"机组缺陷 {entry_id} 不存在或已归档"
        editable = ("缺陷部位", "缺陷等级", "计划消除日", "发现方式", "报告人")
        if row.get("status") != STATUS_ORDER[0]:
            locked = [field for field in ("缺陷部位", "缺陷等级", "计划消除日") if field in values]
            if locked:
                return None, f"缺陷{row.get('缺陷编号', entry_id)}当前为{row.get('status')}，定级字段已锁定，不能再改：{'、'.join(locked)}"
        for field in editable:
            if field in values:
                row[field] = str(values.get(field) or "").strip()
        blocked = missing_grade_fields(row)
        if blocked:
            return self._serialize(row), f"已保存补录内容，但定级仍卡在：{'、'.join(blocked)}"
        return self._serialize(row), f"缺陷{row.get('缺陷编号', entry_id)}补录完成，定级缺项已补齐"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"机组缺陷 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于缺陷登记可执行范围"
        if action == "确认定级":
            return self.confirm_grade(entry_id)
        target = ACTION_RULES[action]
        current_index = STATUS_ORDER.index(entry.get("status")) if entry.get("status") in STATUS_ORDER else -1
        target_index = STATUS_ORDER.index(target)
        if target_index <= current_index:
            return None, f"缺陷{entry.get('缺陷编号', entry_id)}当前为{entry.get('status')}，不能再执行{action}"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self._serialize(entry), f"机组缺陷已{action}"

    # ---------- 批量定级：顺序处理、遇阻即停、断点续跑 ----------

    def grade_pending_batch(self) -> dict[str, Any]:
        """对待定级队列按 id 顺序定级；遇第一条缺项即停并写明断在第几条。

        再次调用时从上一轮断点（仍为待定级的那条）接着走，断点修复后自动通过。
        每轮开始先重算待办，条数永远对应当前数据。
        """
        rows = sorted(
            (row for row in store.rows(MODULE) if row.get("status") == STATUS_ORDER[0]),
            key=lambda row: int(row.get("id", 0)),
        )
        total = len(rows)
        if total == 0:
            self._batch_state = {"done": True, "stopped_id": None}
            return {
                "ok": True,
                "finished": True,
                "message": "待办清单已清空，没有待定级缺陷",
                "队列总数": 0,
                "本次定级": 0,
                "已定级编号": [],
                "断点位置": None,
                "断点编号": None,
                "todo": self.todo_summary(),
            }

        start = 0
        state = self._batch_state
        if state and not state.get("done") and state.get("stopped_id") is not None:
            for index, row in enumerate(rows):
                if int(row.get("id", 0)) == int(state["stopped_id"]):
                    start = index
                    break

        graded_ids: list[str] = []
        for offset, row in enumerate(rows[start:], start=start + 1):
            blocked = missing_grade_fields(row)
            if blocked:
                reason = "、".join(blocked)
                # offset 是该条在“当前待定级队列（按id排序）”中的位置，也是待办清单里的序号。
                self._batch_state = {
                    "done": False,
                    "stopped_id": row["id"],
                    "stopped_position": offset,
                    "stopped_reason": reason,
                }
                return {
                    "ok": False,
                    "finished": False,
                    "message": (
                        f"批量定级中断：当前待办{total}条，本轮定级{len(graded_ids)}条，"
                        f"断在待办第{offset}条（编号{row.get('缺陷编号', row['id'])}），卡在：{reason}。"
                        f"补齐该条后再次执行，将从待办第{offset}条接着走"
                    ),
                    "队列总数": total,
                    "本次定级": len(graded_ids),
                    "已定级编号": graded_ids,
                    "断点位置": offset,
                    "断点编号": row.get("缺陷编号", row["id"]),
                    "断点原因": reason,
                    "todo": self.todo_summary(),
                }
            self._apply_grade(row)
            graded_ids.append(str(row.get("缺陷编号", row["id"])))

        self._batch_state = {"done": True, "stopped_id": None}
        return {
            "ok": True,
            "finished": True,
            "message": f"批量定级完成：队列共{total}条，本次定级{len(graded_ids)}条，待办清单已清空",
            "队列总数": total,
            "本次定级": len(graded_ids),
            "已定级编号": graded_ids,
            "断点位置": None,
            "断点编号": None,
            "todo": self.todo_summary(),
        }
