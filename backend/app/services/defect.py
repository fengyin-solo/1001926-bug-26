"""缺陷登记业务规则：状态流转、定级卡控、超期识别与批量定级任务。

定级阈值全部来自 app.services.defect_rules（唯一一份），
本服务不再自行判断部位/等级对应的时限。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services import defect_rules
from app.store import store

MODULE = "defect"
REQUIRED_FIELDS = ["缺陷编号", "缺陷部位"]
# 确认定级/续跑时允许就地补录的字段
EDITABLE_FIELDS = ["缺陷等级", "计划消除日", "缺陷部位", "发现时间"]
STATUS_ORDER = ["待定级", "已定级", "处置中", "已消除"]
ACTION_RULES = {"确认定级": "已定级", "提交消除": "处置中", "验收消除": "已消除"}
# 每个动作允许出发的状态；不在范围内一律拦下并说明
ACTION_GUARDS = {
    "确认定级": {"待定级"},
    "提交消除": {"已定级"},
    "验收消除": {"处置中"},
}


class DefectService:
    def __init__(self) -> None:
        # 批量定级任务：内存态；重开页面可查到「最新一份」任务接着走
        self.jobs: dict[int, dict[str, Any]] = {}

    # ---------- 读取 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        overdue_only: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        self.refresh_flags()
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("缺陷编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if overdue_only:
            rows = [row for row in rows if defect_rules.evaluate(row)["超期"]]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._serialize(row) for row in rows[start:start + size]], total

    def overdue_entries(self) -> list[dict[str, Any]]:
        """超过计划消除日仍在「处置中」的缺陷，单独列出。"""
        self.refresh_flags()
        rows = [
            self._serialize(row)
            for row in store.rows(MODULE)
            if defect_rules.evaluate(row)["超期"]
        ]
        return sorted(rows, key=lambda item: str(item.get("计划消除日") or ""))

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        self.refresh_flags()
        return self._serialize(row)

    def summary(self) -> dict[str, int]:
        """待办清单计数：每次都按当前数据重算，不沿用任何缓存数字。"""
        self.refresh_flags()
        rows = store.rows(MODULE)
        counts = {"待定级": 0, "已定级": 0, "处置中": 0, "已消除": 0, "超期": 0}
        for row in rows:
            status = str(row.get("status") or "")
            if status in counts:
                counts[status] += 1
            if defect_rules.evaluate(row)["超期"]:
                counts["超期"] += 1
        counts["待办"] = counts["待定级"] + counts["处置中"]
        return counts

    def refresh_flags(self) -> None:
        """把 pending/abnormal 与当前状态、超期判定对齐，供运营概览汇总。"""
        for row in store.rows(MODULE):
            row["pending"] = row.get("status") != STATUS_ORDER[-1]
            row["abnormal"] = defect_rules.evaluate(row)["超期"]

    # ---------- 写入 ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 等级与计划消除日可在定级前补录；登记时缺失不拦，定级时才卡
        for field in ["缺陷等级", "计划消除日", "发现方式", "发现时间", "报告人"]:
            if str(values.get(field) or "").strip():
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._serialize(entry), []

    def update_entry(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """就地补录缺陷等级/计划消除日等，用于解除批量定级时的卡控项。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"机组缺陷 {entry_id} 不存在或已归档"
        changed = [
            field for field in EDITABLE_FIELDS
            if str(values.get(field) or "").strip()
        ]
        if not changed:
            return None, "没有可更新的字段（支持：缺陷等级、计划消除日、缺陷部位、发现时间）"
        if "计划消除日" in changed and defect_rules.parse_day(values.get("计划消除日")) is None:
            return None, "计划消除日日期格式不正确（应为 YYYY-MM-DD），本次未保存"
        if "发现时间" in changed and defect_rules.parse_day(values.get("发现时间")) is None:
            return None, "发现时间日期格式不正确（应为 YYYY-MM-DD），本次未保存"
        if "缺陷等级" in changed and not defect_rules.normalize_level(values.get("缺陷等级")):
            return None, "缺陷等级不在允许范围（仅支持：紧急 / 重大 / 一般），本次未保存"
        for field in changed:
            entry[field] = values.get(field)
        # 阈值口径只有一份，补录后立即重算并回显还卡在哪一项
        return self._serialize(entry), ""

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"机组缺陷 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于缺陷登记可执行范围"

        current = str(entry.get("status") or "")
        allowed = ACTION_GUARDS[action]
        if current not in allowed:
            allowed_text = "、".join(sorted(allowed))
            return None, f"缺陷当前为「{current}」，不能{action}（仅「{allowed_text}」状态可执行）"

        if action == "确认定级":
            blockers = defect_rules.grading_blockers(entry)
            if blockers:
                # 明确告诉使用者卡在哪一项，而不是笼统地报失败
                return None, "无法确认定级：" + "；".join(blockers)
            target = ACTION_RULES[action]
            entry["status"] = target
            # 重复定级只保留最新结论：整段覆盖，不追加历史
            verdict = defect_rules.evaluate(entry)
            entry["定级信息"] = {
                "定级结论": verdict["定级结论"],
                "标准缺陷等级": verdict["标准缺陷等级"],
                "部位分类": verdict["部位分类"],
                "处置时限天数": verdict["处置时限天数"],
                "最迟消除日": verdict["最迟消除日"],
                "定级时间": date.today().isoformat(),
            }
        else:
            entry["status"] = ACTION_RULES[action]

        self.refresh_flags()
        return self._serialize(entry), f"机组缺陷已{action}"

    # ---------- 批量定级（可中断、可续跑） ----------

    def start_grading_job(self) -> tuple[dict[str, Any] | None, str]:
        targets = [
            row for row in sorted(store.rows(MODULE), key=lambda item: int(item.get("id", 0)))
            if row.get("status") == "待定级"
        ]
        if not targets:
            return None, "没有待定级缺陷，批量定级无需启动"
        job_id = max(self.jobs, default=0) + 1
        job: dict[str, Any] = {
            "id": job_id,
            "status": "进行中",
            "targets": [int(row["id"]) for row in targets],
            "items": [None] * len(targets),
            "cursor": 0,  # 下一条待处理下标（0 基）
            "中断说明": "",
        }
        self.jobs[job_id] = job
        self._advance(job)
        return self._job_view(job), ""

    def resume_grading_job(self, job_id: int) -> tuple[dict[str, Any] | None, str]:
        job = self.jobs.get(job_id)
        if job is None:
            return None, f"定级任务 {job_id} 不存在（服务重启后内存任务会清空，可重新发起批量定级）"
        if job["status"] == "已完成":
            return self._job_view(job), "任务已完成，无需续跑"
        if job["status"] == "进行中":
            return self._job_view(job), "任务正在处理中"
        self._advance(job)
        return self._job_view(job), ""

    def latest_job(self) -> dict[str, Any] | None:
        if not self.jobs:
            return None
        return self._job_view(self.jobs[max(self.jobs)])

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        return self._job_view(job) if job else None

    def _advance(self, job: dict[str, Any]) -> None:
        """从 cursor 继续逐条定级；遇卡控项即中断，cursor 不动，续跑重试同一条。"""
        targets: list[int] = job["targets"]
        while job["cursor"] < len(targets):
            index = job["cursor"]
            entry_id = targets[index]
            row = store.find(MODULE, entry_id)
            if row is None or row.get("status") != "待定级":
                # 期间已被手工处理/归档：跳过而不是当成失败
                job["items"][index] = {
                    "序号": index + 1,
                    "缺陷编号": row.get("缺陷编号") if row else f"id={entry_id}",
                    "结果": "已跳过",
                    "说明": "该缺陷已不是待定级状态",
                }
                job["cursor"] = index + 1
                continue

            _entry, message = self.run_action(entry_id, "确认定级")
            if _entry is None:
                # 断在第几条要写清：序号 + 编号 + 卡控项原文
                job["status"] = "已中断"
                job["中断说明"] = f"断在第 {index + 1} / {len(targets)} 条：{message}"
                job["items"][index] = {
                    "序号": index + 1,
                    "缺陷编号": row.get("缺陷编号"),
                    "结果": "已中断",
                    "说明": message,
                }
                job["待办统计"] = self.summary()
                return

            verdict = defect_rules.evaluate(store.find(MODULE, entry_id))
            job["items"][index] = {
                "序号": index + 1,
                "缺陷编号": row.get("缺陷编号"),
                "结果": "已定级",
                "说明": verdict["定级结论"],
            }
            job["cursor"] = index + 1

        job["status"] = "已完成"
        job["中断说明"] = ""
        job["待办统计"] = self.summary()

    def _job_view(self, job: dict[str, Any]) -> dict[str, Any]:
        total = len(job["targets"])
        cursor = int(job["cursor"])
        graded = sum(1 for item in job["items"] if item and item["结果"] == "已定级")
        view = {
            "id": job["id"],
            "status": job["status"],
            "total": total,
            "processed": graded,
            "cursor": cursor,
            "position": min(cursor + 1, total) if total else 0,
            "中断说明": job.get("中断说明", ""),
            "items": [item for item in job["items"] if item],
            "待办统计": job.get("待办统计", self.summary()),
        }
        if job["status"] == "已中断":
            current_id = job["targets"][cursor] if cursor < total else None
            view["current_entry_id"] = current_id
        return view

    # ---------- 序列化 ----------

    def _serialize(self, row: dict[str, Any]) -> dict[str, Any]:
        """列表与详情共用同一份出参结构，判定结论只来自 evaluate()。"""
        item = dict(row)
        item["判定"] = defect_rules.evaluate(row)
        return item


# 路由与 main 共享同一个实例（含批量任务内存态）
defect_service = DefectService()
