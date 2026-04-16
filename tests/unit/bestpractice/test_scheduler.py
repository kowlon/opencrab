"""Tests for TaskScheduler abstraction and LinearScheduler."""
import pytest
from seeagent.bestpractice.models import (
    BestPracticeConfig, SubtaskConfig, SubtaskStatus,
    BPInstanceSnapshot, RunMode,
)
from seeagent.bestpractice.engine import LinearScheduler


def _make_config(subtask_count=3, input_mappings=None):
    subtasks = []
    for i in range(subtask_count):
        sid = f"s{i+1}"
        mapping = (input_mappings or {}).get(sid, {})
        subtasks.append(SubtaskConfig(
            id=sid, name=f"Step {i+1}", agent_profile="default",
            input_schema={
                "type": "object",
                "properties": {"data": {"type": "string"}},
                "required": ["data"],
            },
            input_mapping=mapping,
        ))
    return BestPracticeConfig(
        id="test_bp", name="Test BP", subtasks=subtasks,
        final_output_schema={
            "type": "object",
            "properties": {"result": {"type": "string"}},
        },
    )


def _make_snapshot(bp_id="test_bp", session_id="sess", subtask_ids=("s1", "s2", "s3"),
                   current_index=0, statuses=None, outputs=None,
                   initial_input=None, supplemented_inputs=None):
    sts = statuses or {sid: SubtaskStatus.PENDING.value for sid in subtask_ids}
    return BPInstanceSnapshot(
        bp_id=bp_id, instance_id="bp-test123", session_id=session_id,
        created_at=0.0, current_subtask_index=current_index,
        subtask_statuses=sts, initial_input=initial_input or {},
        subtask_outputs=outputs or {}, context_summary="",
        supplemented_inputs=supplemented_inputs or {},
    )


class TestLinearSchedulerGetReadyTasks:
    def test_first_task_ready(self):
        cfg = _make_config()
        snap = _make_snapshot()
        sched = LinearScheduler(cfg, snap)
        ready = sched.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "s1"

    def test_no_ready_when_all_done(self):
        cfg = _make_config()
        snap = _make_snapshot(
            current_index=3,
            statuses={"s1": "done", "s2": "done", "s3": "done"},
        )
        sched = LinearScheduler(cfg, snap)
        assert sched.get_ready_tasks() == []

    def test_stale_task_is_ready(self):
        cfg = _make_config()
        snap = _make_snapshot(
            current_index=1,
            statuses={"s1": "done", "s2": "stale", "s3": "pending"},
        )
        sched = LinearScheduler(cfg, snap)
        ready = sched.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "s2"

    def test_current_task_not_ready(self):
        cfg = _make_config()
        snap = _make_snapshot(
            current_index=0,
            statuses={"s1": "current", "s2": "pending", "s3": "pending"},
        )
        sched = LinearScheduler(cfg, snap)
        assert sched.get_ready_tasks() == []

    def test_waiting_input_is_ready(self):
        """WAITING_INPUT 状态的子任务被视为 ready。

        这样 bp_next/advance 被再次触发时能重新检查 input 完整性，
        若仍缺失则重发 bp_ask_user，而不是静默无响应。
        """
        cfg = _make_config()
        snap = _make_snapshot(
            current_index=0,
            statuses={"s1": "waiting_input", "s2": "pending", "s3": "pending"},
        )
        sched = LinearScheduler(cfg, snap)
        ready = sched.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "s1"


class TestLinearSchedulerCompleteTask:
    def test_complete_advances_index(self):
        cfg = _make_config()
        snap = _make_snapshot()
        sched = LinearScheduler(cfg, snap)
        sched.complete_task("s1", {"data": "output1"})
        assert snap.subtask_statuses["s1"] == SubtaskStatus.DONE.value
        assert snap.subtask_outputs["s1"] == {"data": "output1"}
        assert snap.current_subtask_index == 1

    def test_complete_last_task(self):
        cfg = _make_config()
        snap = _make_snapshot(
            current_index=2,
            statuses={"s1": "done", "s2": "done", "s3": "pending"},
        )
        sched = LinearScheduler(cfg, snap)
        sched.complete_task("s3", {"result": "final"})
        assert snap.current_subtask_index == 3
        assert sched.is_done()


class TestLinearSchedulerResolveInput:
    def test_first_subtask_uses_initial_input(self):
        cfg = _make_config()
        snap = _make_snapshot(initial_input={"data": "user_input"})
        sched = LinearScheduler(cfg, snap)
        assert sched.resolve_input("s1") == {"data": "user_input"}

    def test_subsequent_subtask_uses_previous_output(self):
        cfg = _make_config()
        snap = _make_snapshot(current_index=1, outputs={"s1": {"data": "step1_out"}})
        sched = LinearScheduler(cfg, snap)
        assert sched.resolve_input("s2") == {"data": "step1_out"}

    def test_input_mapping_overrides_linear_fallback(self):
        cfg = _make_config(input_mappings={"s3": {"upstream_data": "s1"}})
        snap = _make_snapshot(current_index=2, outputs={"s1": {"x": 1}, "s2": {"y": 2}})
        sched = LinearScheduler(cfg, snap)
        assert sched.resolve_input("s3") == {"upstream_data": {"x": 1}}

    def test_supplemented_inputs_merged_linear(self):
        """R19: supplemented_inputs merged in linear fallback path."""
        cfg = _make_config()
        snap = _make_snapshot(
            initial_input={"data": "original"},
            supplemented_inputs={"s1": {"extra": "added"}},
        )
        sched = LinearScheduler(cfg, snap)
        assert sched.resolve_input("s1") == {"data": "original", "extra": "added"}

    def test_supplemented_inputs_merged_with_input_mapping(self):
        """R19: supplemented_inputs merged even when input_mapping is used."""
        cfg = _make_config(input_mappings={"s2": {"from_s1": "s1"}})
        snap = _make_snapshot(
            current_index=1, outputs={"s1": {"a": 1}},
            supplemented_inputs={"s2": {"manual_field": "user_value"}},
        )
        sched = LinearScheduler(cfg, snap)
        assert sched.resolve_input("s2") == {
            "from_s1": {"a": 1}, "manual_field": "user_value",
        }

    def test_unknown_subtask_returns_empty(self):
        cfg = _make_config()
        snap = _make_snapshot()
        sched = LinearScheduler(cfg, snap)
        assert sched.resolve_input("nonexistent") == {}


class TestLinearSchedulerIsDone:
    def test_not_done_with_pending(self):
        cfg = _make_config(subtask_count=2)
        snap = _make_snapshot(
            subtask_ids=("s1", "s2"), statuses={"s1": "done", "s2": "pending"},
        )
        sched = LinearScheduler(cfg, snap)
        assert not sched.is_done()

    def test_done_when_all_complete(self):
        cfg = _make_config(subtask_count=2)
        snap = _make_snapshot(
            subtask_ids=("s1", "s2"), statuses={"s1": "done", "s2": "done"},
        )
        sched = LinearScheduler(cfg, snap)
        assert sched.is_done()


class TestDeriveOutputSchema:
    def test_middle_subtask_no_upstream_returns_none(self):
        """下一个 subtask 没有 upstream → 返回 None（上游不需要给下游输出）。"""
        cfg = _make_config()
        snap = _make_snapshot()
        sched = LinearScheduler(cfg, snap)
        assert sched.derive_output_schema("s1") is None

    def test_last_subtask_gets_final_output_schema(self):
        cfg = _make_config()
        snap = _make_snapshot()
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s3")
        assert schema == {"type": "object", "properties": {"result": {"type": "string"}}}

    def test_no_final_schema_returns_none(self):
        cfg = _make_config()
        cfg.final_output_schema = None
        snap = _make_snapshot()
        sched = LinearScheduler(cfg, snap)
        assert sched.derive_output_schema("s3") is None


def _make_config_with_upstream():
    """Build a 3-subtask config where s2 has upstream, s3 does not."""
    s1 = SubtaskConfig(
        id="s1", name="Step 1", agent_profile="default",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )
    s2 = SubtaskConfig(
        id="s2", name="Step 2", agent_profile="default",
        input_schema={
            "type": "object",
            "properties": {
                "results": {"type": "array", "description": "上游搜索结果"},
                "focus": {"type": "string", "description": "用户指定的关注点"},
            },
            "required": ["results", "focus"],
            "upstream": ["results"],
        },
    )
    s3 = SubtaskConfig(
        id="s3", name="Step 3", agent_profile="default",
        input_schema={
            "type": "object",
            "properties": {"analysis": {"type": "object"}},
            "required": ["analysis"],
        },
    )
    return BestPracticeConfig(
        id="test_upstream", name="Test Upstream", subtasks=[s1, s2, s3],
        final_output_schema={"type": "object", "properties": {"report": {"type": "string"}}},
    )


class TestDeriveOutputSchemaWithUpstream:
    def test_upstream_filters_output_schema(self):
        """有 upstream 时，derive_output_schema 只返回 upstream 字段。"""
        cfg = _make_config_with_upstream()
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s1")
        # s2 有 upstream=["results"]，所以 s1 的输出 schema 只含 results
        assert schema == {
            "type": "object",
            "properties": {"results": {"type": "array", "description": "上游搜索结果"}},
            "required": ["results"],
        }

    def test_no_upstream_returns_none(self):
        """没有 upstream 时，返回 None（上游不需要给下游输出）。"""
        cfg = _make_config_with_upstream()
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        # s3 没有 upstream → s2 的输出 schema 为 None
        assert sched.derive_output_schema("s2") is None

    def test_last_subtask_still_uses_final_schema(self):
        """最后一个 subtask 仍使用 final_output_schema。"""
        cfg = _make_config_with_upstream()
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s3")
        assert schema == {"type": "object", "properties": {"report": {"type": "string"}}}

    def test_empty_upstream_returns_none(self):
        """upstream=[] 等同于未配置，返回 None。"""
        cfg = _make_config_with_upstream()
        cfg.subtasks[1].input_schema["upstream"] = []
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        assert sched.derive_output_schema("s1") is None


def _make_config_with_branch_upstream():
    """Build a config where s2 has oneOf branches with different upstream."""
    s1 = SubtaskConfig(
        id="s1", name="数据获取", agent_profile="default",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )
    s2 = SubtaskConfig(
        id="s2", name="分析", agent_profile="default",
        input_schema={
            "type": "object",
            "oneOf": [
                {
                    "title": "按相机分析",
                    "properties": {
                        "camera_ids": {"type": "array"},
                        "feature_text": {"type": "string"},
                    },
                    "required": ["camera_ids", "feature_text"],
                    "upstream": ["camera_ids"],
                },
                {
                    "title": "按区域分析",
                    "properties": {
                        "area_code": {"type": "string"},
                        "feature_text": {"type": "string"},
                    },
                    "required": ["area_code", "feature_text"],
                    "upstream": ["area_code"],
                },
            ],
        },
    )
    s3 = SubtaskConfig(
        id="s3", name="可视化", agent_profile="default",
        input_schema={
            "type": "object",
            "properties": {"result": {"type": "object"}},
            "required": ["result"],
            "upstream": ["result"],
        },
    )
    return BestPracticeConfig(
        id="test_branch_upstream", name="Test Branch Upstream",
        subtasks=[s1, s2, s3],
    )


class TestDeriveOutputSchemaWithBranchUpstream:
    def test_branch_upstream_union(self):
        """分支内 upstream 取并集。"""
        cfg = _make_config_with_branch_upstream()
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s1")
        assert schema is not None
        assert set(schema["properties"].keys()) == {"camera_ids", "area_code"}
        assert set(schema["required"]) == {"camera_ids", "area_code"}

    def test_top_level_upstream_takes_priority(self):
        """顶层 upstream 优先于分支内 upstream。"""
        cfg = _make_config_with_branch_upstream()
        # 给 s2 加顶层 upstream，应该优先使用
        cfg.subtasks[1].input_schema["upstream"] = ["camera_ids"]
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s1")
        assert schema is not None
        assert set(schema["properties"].keys()) == {"camera_ids"}
        assert schema["required"] == ["camera_ids"]

    def test_no_branch_upstream_returns_none(self):
        """分支内无 upstream 时返回 None。"""
        cfg = _make_config_with_branch_upstream()
        # 去掉所有分支的 upstream
        for branch in cfg.subtasks[1].input_schema["oneOf"]:
            del branch["upstream"]
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        assert sched.derive_output_schema("s1") is None

    def test_flat_upstream_still_works(self):
        """s3 有顶层 upstream，回归验证不受影响。"""
        cfg = _make_config_with_branch_upstream()
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s2")
        assert schema is not None
        assert set(schema["properties"].keys()) == {"result"}
        assert schema["required"] == ["result"]


def _make_config_with_upstream_from():
    """3 个子任务; final_output_schema 声明 upstream_from 把 data 字段从 s2 聚合。"""
    s1 = SubtaskConfig(
        id="s1", name="Step 1", agent_profile="default",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
    )
    s2 = SubtaskConfig(
        id="s2", name="Step 2", agent_profile="default",
        input_schema={"type": "object", "properties": {"data": {"type": "array"}}, "required": ["data"]},
    )
    s3 = SubtaskConfig(
        id="s3", name="Step 3", agent_profile="default",
        input_schema={"type": "object", "properties": {"data": {"type": "array"}}, "required": ["data"]},
    )
    return BestPracticeConfig(
        id="test_upstream_from", name="Test upstream_from", subtasks=[s1, s2, s3],
        final_output_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "报告标题"},
                "data": {"type": "array", "description": "数据列表"},
                "summary": {"type": "string", "description": "总结"},
            },
            "required": ["title", "data"],
            "upstream_from": {"data": "s2"},
        },
    )


class TestDeriveOutputSchemaWithUpstreamFrom:
    """验证 final_output_schema.upstream_from 对最后子任务输出 schema 的过滤。"""

    def test_last_subtask_filters_upstream_from_fields(self):
        """最后子任务的 output schema 应过滤掉 upstream_from 声明的字段。"""
        cfg = _make_config_with_upstream_from()
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s3")
        assert schema is not None
        # data 应被过滤掉,只剩 title / summary
        assert set(schema["properties"].keys()) == {"title", "summary"}
        assert schema["required"] == ["title"]
        # upstream_from 本身不应出现在返回结果里
        assert "upstream_from" not in schema

    def test_no_upstream_from_returns_schema_as_is(self):
        """没有 upstream_from 时,行为与旧版一致(原 schema 返回)。"""
        cfg = _make_config_with_upstream_from()
        # 去掉 upstream_from,模拟老 BP
        del cfg.final_output_schema["upstream_from"]
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s3")
        assert schema is not None
        assert set(schema["properties"].keys()) == {"title", "data", "summary"}
        assert set(schema["required"]) == {"title", "data"}

    def test_upstream_from_non_dict_ignored(self):
        """upstream_from 不是 dict 时(比如 list),应被忽略,走原路径。"""
        cfg = _make_config_with_upstream_from()
        cfg.final_output_schema["upstream_from"] = ["data"]  # 错误格式
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s3")
        assert schema is not None
        # 未触发过滤,保持原样
        assert set(schema["properties"].keys()) == {"title", "data", "summary"}


class TestAggregateUpstreamFrom:
    """验证 LinearScheduler.complete_task 调用 _aggregate_upstream_from 的行为。"""

    def test_aggregates_missing_field_from_source_subtask(self):
        """最后子任务 output 缺失 upstream_from 声明的字段时,引擎从 source subtask 聚合。"""
        cfg = _make_config_with_upstream_from()
        snap = _make_snapshot(
            subtask_ids=("s1", "s2", "s3"),
            outputs={
                "s2": {"data": [{"id": 1}, {"id": 2}, {"id": 3}]},
            },
            statuses={"s1": "done", "s2": "done", "s3": "pending"},
            current_index=2,
        )
        sched = LinearScheduler(cfg, snap)
        # LLM 只产出了 title 和 summary,没有 data
        llm_output = {"title": "Report", "summary": "done"}
        sched.complete_task("s3", llm_output)
        merged = snap.subtask_outputs["s3"]
        assert merged["title"] == "Report"
        assert merged["summary"] == "done"
        # data 字段应由引擎从 s2 聚合而来
        assert merged["data"] == [{"id": 1}, {"id": 2}, {"id": 3}]

    def test_llm_output_takes_priority_over_aggregation(self):
        """如果 LLM 已经产出字段,引擎不覆盖(LLM 的版本优先)。"""
        cfg = _make_config_with_upstream_from()
        snap = _make_snapshot(
            subtask_ids=("s1", "s2", "s3"),
            outputs={"s2": {"data": [{"id": "from_s2"}]}},
            statuses={"s1": "done", "s2": "done", "s3": "pending"},
            current_index=2,
        )
        sched = LinearScheduler(cfg, snap)
        llm_output = {"title": "Report", "data": [{"id": "from_llm"}], "summary": "done"}
        sched.complete_task("s3", llm_output)
        assert snap.subtask_outputs["s3"]["data"] == [{"id": "from_llm"}]

    def test_missing_source_field_skipped_with_warning(self, caplog):
        """source subtask output 里没有对应字段时,跳过聚合并记录 warning。"""
        import logging
        caplog.set_level(logging.WARNING)
        cfg = _make_config_with_upstream_from()
        snap = _make_snapshot(
            subtask_ids=("s1", "s2", "s3"),
            outputs={"s2": {"other_field": "something"}},  # 没有 "data" 字段
            statuses={"s1": "done", "s2": "done", "s3": "pending"},
            current_index=2,
        )
        sched = LinearScheduler(cfg, snap)
        llm_output = {"title": "Report", "summary": "done"}
        sched.complete_task("s3", llm_output)
        # 聚合跳过,最终 output 里没有 data
        assert "data" not in snap.subtask_outputs["s3"]
        # 应该有 warning 日志
        assert any("upstream_from" in rec.message for rec in caplog.records)

    def test_non_last_subtask_no_aggregation(self):
        """非最后子任务的 complete_task 不触发聚合逻辑。"""
        cfg = _make_config_with_upstream_from()
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        s1_output = {"q": "hello"}
        sched.complete_task("s1", s1_output)
        # s1 的 output 原样保留,没有额外字段
        assert snap.subtask_outputs["s1"] == {"q": "hello"}

    def test_no_upstream_from_no_op(self):
        """没有 upstream_from 声明时,complete_task 等同于直接存储。"""
        cfg = _make_config_with_upstream_from()
        del cfg.final_output_schema["upstream_from"]
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        output = {"title": "R", "summary": "s"}
        sched.complete_task("s3", output)
        # 无聚合,原样存储
        assert snap.subtask_outputs["s3"] == {"title": "R", "summary": "s"}


def _make_config_with_upstream_from_self_sufficient():
    """3 个子任务;只在 final_output_schema 里用 upstream_from,s3 的 input_schema
    完全不声明 upstream(验证方案 D: upstream_from 自给自足)。"""
    s1 = SubtaskConfig(
        id="s1", name="Step 1", agent_profile="default",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
    )
    s2 = SubtaskConfig(
        id="s2", name="Step 2", agent_profile="default",
        input_schema={
            "type": "object",
            "properties": {"data": {"type": "array", "description": "raw data"}},
            "required": ["data"],
        },
    )
    s3 = SubtaskConfig(
        id="s3", name="Step 3", agent_profile="default",
        # 注意: s3 的 input_schema 完全没有 upstream 声明
        input_schema={"type": "object", "properties": {"note": {"type": "string"}}},
    )
    return BestPracticeConfig(
        id="test_sscope", name="Test Self-Sufficient", subtasks=[s1, s2, s3],
        final_output_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "data": {"type": "array", "description": "上游聚合数据"},
                "summary": {"type": "string"},
            },
            "required": ["title", "data"],
            "upstream_from": {"data": "s2"},
        },
    )


class TestPlanDSelfSufficientUpstreamFrom:
    """方案 D 验证: upstream_from 独立于下游 input_schema.upstream 工作。"""

    def test_source_subtask_output_schema_includes_upstream_from_fields(self):
        """当 final_output_schema.upstream_from 指向 s2 时,
        derive_output_schema('s2') 应该包含 'data' 字段,即使 s3.input_schema
        完全没有 upstream 声明。"""
        cfg = _make_config_with_upstream_from_self_sufficient()
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s2")
        assert schema is not None, "方案 D 应该让 upstream_from 独立驱动 s2 产出"
        assert "data" in schema["properties"]
        assert "data" in schema["required"]
        # 字段描述应从 final_output_schema.properties 拿到
        assert schema["properties"]["data"]["description"] == "上游聚合数据"

    def test_downstream_upstream_not_required_for_upstream_from(self):
        """s3 的 input_schema 完全没有 upstream 声明,方案 D 依然让 upstream_from 生效。"""
        cfg = _make_config_with_upstream_from_self_sufficient()
        # 显式确认 s3 没有任何 upstream 声明
        assert "upstream" not in cfg.subtasks[2].input_schema
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s2")
        assert schema is not None
        assert schema["required"] == ["data"]
        assert set(schema["properties"].keys()) == {"data"}

    def test_union_of_downstream_upstream_and_upstream_from(self):
        """如果 s3 有自己的 upstream,upstream_from 的字段应与之取并集(不重复)。"""
        cfg = _make_config_with_upstream_from_self_sufficient()
        # 给 s3 加 upstream 声明 (声明 data 字段,与 upstream_from 重复)
        cfg.subtasks[2].input_schema["upstream"] = ["data"]
        cfg.subtasks[2].input_schema["properties"] = {
            "data": {"type": "array", "description": "from downstream"},
        }
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s2")
        assert schema is not None
        assert schema["required"] == ["data"]  # 去重,不重复
        # 下游声明优先(description 来自 s3.input_schema,而非 final_output_schema)
        assert schema["properties"]["data"]["description"] == "from downstream"

    def test_upstream_from_targeting_middle_not_last_subtask_source(self):
        """upstream_from 指向 s1(不是最后子任务的上一个),也应生效。"""
        cfg = _make_config_with_upstream_from_self_sufficient()
        cfg.final_output_schema["upstream_from"] = {"data": "s1"}
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema_s1 = sched.derive_output_schema("s1")
        # s1 被要求输出 data(来自 upstream_from)
        assert schema_s1 is not None
        assert "data" in schema_s1["properties"]
        # s2 没被 upstream_from 指向,且 s3.input_schema 也没 upstream → None
        schema_s2 = sched.derive_output_schema("s2")
        assert schema_s2 is None

    def test_no_schema_sources_returns_none(self):
        """downstream 没有 upstream + upstream_from 没指向本 subtask → None。"""
        cfg = _make_config_with_upstream_from_self_sufficient()
        # upstream_from 只指向 s2,不指向 s1
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema_s1 = sched.derive_output_schema("s1")
        # s2.input_schema 有 required=[data] 但没有 upstream 声明 → s1 没产出要求
        assert schema_s1 is None

    def test_removing_downstream_upstream_still_works_for_upstream_from(self):
        """关键用例: 未来重构 result-visualize 不再需要 frame_results 作 input 时,
        upstream_from 依然能确保 frame-search 产出 frame_results。"""
        cfg = _make_config_with_upstream_from_self_sufficient()
        # 模拟原本 s3 需要 data 作为 upstream,后续重构去掉
        cfg.subtasks[2].input_schema = {
            "type": "object",
            "properties": {"note": {"type": "string"}},
            # 没有 upstream 声明,也没有 data 字段
        }
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        # 方案 D: upstream_from 依然独立驱动 s2 产出 data
        schema_s2 = sched.derive_output_schema("s2")
        assert schema_s2 is not None
        assert "data" in schema_s2["properties"]
        assert schema_s2["properties"]["data"]["description"] == "上游聚合数据"


class TestDeriveOutputSchemaPreservesMetadata:
    """Review #1 修复: derive_output_schema 在 upstream_from 过滤时应保留原 schema 的
    所有顶层元信息(description/additionalProperties/oneOf/anyOf/自定义 x-* 等),
    仅过滤 properties 和 required,并移除 upstream_from 内部 marker。"""

    def _make_cfg_with_rich_final_schema(self, extra_top_keys: dict):
        """构造一个带丰富顶层元信息的 final_output_schema 的 3 子任务 BP。"""
        s1 = SubtaskConfig(
            id="s1", name="S1", agent_profile="a",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )
        s2 = SubtaskConfig(
            id="s2", name="S2", agent_profile="b",
            input_schema={"type": "object", "properties": {"d": {"type": "array"}}},
        )
        s3 = SubtaskConfig(
            id="s3", name="S3", agent_profile="c",
            input_schema={"type": "object", "properties": {"note": {"type": "string"}}},
        )
        final_schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "data": {"type": "array"},
                "summary": {"type": "string"},
            },
            "required": ["title", "data"],
            "upstream_from": {"data": "s2"},
            **extra_top_keys,
        }
        return BestPracticeConfig(
            id="bp_meta", name="Test Metadata Preservation",
            subtasks=[s1, s2, s3],
            final_output_schema=final_schema,
        )

    def test_preserves_description(self):
        """顶层 description 字段应在过滤后保留。"""
        cfg = self._make_cfg_with_rich_final_schema({
            "description": "This is a top-level schema description",
        })
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s3")
        assert schema is not None
        assert schema.get("description") == "This is a top-level schema description"
        # data 依然应被过滤
        assert "data" not in schema["properties"]
        assert "data" not in schema["required"]

    def test_preserves_additional_properties(self):
        """additionalProperties 字段应保留。"""
        cfg = self._make_cfg_with_rich_final_schema({
            "additionalProperties": False,
        })
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s3")
        assert schema is not None
        assert schema.get("additionalProperties") is False

    def test_preserves_oneof(self):
        """oneOf 分支应保留。"""
        cfg = self._make_cfg_with_rich_final_schema({
            "oneOf": [
                {"properties": {"mode_a": {"type": "string"}}},
                {"properties": {"mode_b": {"type": "number"}}},
            ],
        })
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s3")
        assert schema is not None
        assert "oneOf" in schema
        assert len(schema["oneOf"]) == 2
        assert schema["oneOf"][0]["properties"] == {"mode_a": {"type": "string"}}

    def test_preserves_custom_extension_fields(self):
        """自定义 x-* 扩展字段(OpenAPI/JSON-Schema 自定义扩展惯例)应保留。"""
        cfg = self._make_cfg_with_rich_final_schema({
            "x-custom-marker": "preserved",
            "x-internal-hint": {"nested": "data"},
        })
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s3")
        assert schema is not None
        assert schema.get("x-custom-marker") == "preserved"
        assert schema.get("x-internal-hint") == {"nested": "data"}

    def test_removes_upstream_from_marker_from_returned_schema(self):
        """upstream_from 是引擎内部 marker,返回给 LLM 看的 schema 里不应出现。"""
        cfg = self._make_cfg_with_rich_final_schema({})
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        schema = sched.derive_output_schema("s3")
        assert schema is not None
        assert "upstream_from" not in schema  # 必须被 pop 掉
        # 原 config 的 final_output_schema 不应被污染
        assert "upstream_from" in cfg.final_output_schema

    def test_original_schema_not_mutated(self):
        """derive_output_schema 不应修改原 final_output_schema(浅拷贝隔离)。"""
        cfg = self._make_cfg_with_rich_final_schema({"description": "original"})
        original_keys = set(cfg.final_output_schema.keys())
        original_props = set(cfg.final_output_schema["properties"].keys())
        original_required = list(cfg.final_output_schema["required"])
        snap = _make_snapshot(subtask_ids=("s1", "s2", "s3"))
        sched = LinearScheduler(cfg, snap)
        _ = sched.derive_output_schema("s3")
        # 原 schema 的顶层 keys 不变
        assert set(cfg.final_output_schema.keys()) == original_keys
        # 原 properties 不变(关键:浅拷贝 dict 顶层后,修改 filtered["properties"] 不影响原)
        assert set(cfg.final_output_schema["properties"].keys()) == original_props
        # 原 required 不变
        assert cfg.final_output_schema["required"] == original_required


class TestAggregateUpstreamFromDefensive:
    """Review #2 修复: _aggregate_upstream_from 对非 dict 的 source_output 应优雅降级,
    不应抛 AttributeError 或触发奇怪的 in 运算符行为。"""

    def test_source_output_is_string_does_not_crash(self, caplog):
        """source_output 是 str 时应打 warning 并跳过,不抛异常。"""
        import logging
        caplog.set_level(logging.WARNING)
        cfg = _make_config_with_upstream_from()
        snap = _make_snapshot(
            subtask_ids=("s1", "s2", "s3"),
            outputs={"s2": "not-a-dict-oops"},  # 违反类型合约
            statuses={"s1": "done", "s2": "done", "s3": "pending"},
            current_index=2,
        )
        sched = LinearScheduler(cfg, snap)
        llm_output = {"title": "R", "summary": "s"}
        # 应该不抛异常
        sched.complete_task("s3", llm_output)
        final = snap.subtask_outputs["s3"]
        assert "data" not in final  # 未聚合,因为 source 非 dict
        # 应该有 warning 提示类型问题
        assert any(
            "not a dict" in rec.message and "str" in rec.message
            for rec in caplog.records
        ), f"expected 'not a dict' warning about str, got: {[r.message for r in caplog.records]}"

    def test_source_output_is_list_does_not_crash(self, caplog):
        """source_output 是 list 时应打 warning 并跳过。"""
        import logging
        caplog.set_level(logging.WARNING)
        cfg = _make_config_with_upstream_from()
        snap = _make_snapshot(
            subtask_ids=("s1", "s2", "s3"),
            outputs={"s2": [1, 2, 3]},  # 违反类型合约
            statuses={"s1": "done", "s2": "done", "s3": "pending"},
            current_index=2,
        )
        sched = LinearScheduler(cfg, snap)
        llm_output = {"title": "R", "summary": "s"}
        sched.complete_task("s3", llm_output)
        final = snap.subtask_outputs["s3"]
        assert "data" not in final
        assert any(
            "not a dict" in rec.message and "list" in rec.message
            for rec in caplog.records
        )

    def test_source_output_is_none_does_not_crash(self, caplog):
        """source_output 显式为 None 时应打 warning 并跳过。"""
        import logging
        caplog.set_level(logging.WARNING)
        cfg = _make_config_with_upstream_from()
        snap = _make_snapshot(
            subtask_ids=("s1", "s2", "s3"),
            outputs={"s2": None},  # 违反类型合约
            statuses={"s1": "done", "s2": "done", "s3": "pending"},
            current_index=2,
        )
        sched = LinearScheduler(cfg, snap)
        llm_output = {"title": "R"}
        sched.complete_task("s3", llm_output)
        final = snap.subtask_outputs["s3"]
        assert "data" not in final
        assert any(
            "not a dict" in rec.message and "NoneType" in rec.message
            for rec in caplog.records
        )

    def test_normal_dict_source_still_aggregates(self):
        """正常 dict 路径不应被防御性检查干扰(回归测试)。"""
        cfg = _make_config_with_upstream_from()
        snap = _make_snapshot(
            subtask_ids=("s1", "s2", "s3"),
            outputs={"s2": {"data": [{"id": 1}, {"id": 2}]}},
            statuses={"s1": "done", "s2": "done", "s3": "pending"},
            current_index=2,
        )
        sched = LinearScheduler(cfg, snap)
        sched.complete_task("s3", {"title": "R"})
        assert snap.subtask_outputs["s3"]["data"] == [{"id": 1}, {"id": 2}]
