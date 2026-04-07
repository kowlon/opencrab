"""Tests for schema utility functions: collect_all_properties, collect_all_required, collect_all_upstream."""

from seeagent.bestpractice.models import (
    collect_all_properties,
    collect_all_required,
    collect_all_upstream,
)


# ── collect_all_properties ─────────────────────────────────────


class TestCollectAllProperties:
    def test_flat_schema(self):
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        }
        assert collect_all_properties(schema) == schema["properties"]

    def test_empty_schema(self):
        assert collect_all_properties({}) == {}
        assert collect_all_properties({"type": "object"}) == {}

    def test_oneOf_branches(self):
        schema = {
            "type": "object",
            "oneOf": [
                {"properties": {"x": {"type": "string"}}, "required": ["x"]},
                {"properties": {"y": {"type": "integer"}}, "required": ["y"]},
            ],
        }
        props = collect_all_properties(schema)
        assert set(props.keys()) == {"x", "y"}

    def test_anyOf_branches(self):
        schema = {
            "type": "object",
            "anyOf": [
                {"properties": {"a": {"type": "string"}}},
                {"properties": {"b": {"type": "number"}}},
            ],
        }
        props = collect_all_properties(schema)
        assert set(props.keys()) == {"a", "b"}

    def test_mixed_top_level_and_branches(self):
        schema = {
            "type": "object",
            "properties": {"shared": {"type": "string"}},
            "oneOf": [
                {"properties": {"branch_a": {"type": "string"}}},
                {"properties": {"branch_b": {"type": "integer"}}},
            ],
        }
        props = collect_all_properties(schema)
        assert set(props.keys()) == {"shared", "branch_a", "branch_b"}

    def test_branch_overrides_top_level(self):
        schema = {
            "type": "object",
            "properties": {"field": {"type": "string", "description": "top"}},
            "oneOf": [
                {"properties": {"field": {"type": "array", "description": "branch"}}},
            ],
        }
        props = collect_all_properties(schema)
        assert props["field"]["type"] == "array"

    def test_real_camera_search_schema(self):
        schema = {
            "type": "object",
            "oneOf": [
                {
                    "title": "语义搜索",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
                {
                    "title": "POI 范围检索",
                    "properties": {
                        "keyword": {"type": "string"},
                        "radius_m": {"type": "number"},
                    },
                    "required": ["keyword"],
                },
            ],
        }
        props = collect_all_properties(schema)
        assert set(props.keys()) == {"query", "limit", "keyword", "radius_m"}


# ── collect_all_required ───────────────────────────────────────


class TestCollectAllRequired:
    def test_flat_schema(self):
        schema = {"required": ["a", "b"]}
        assert collect_all_required(schema) == {"a", "b"}

    def test_empty(self):
        assert collect_all_required({}) == set()

    def test_oneOf_branches(self):
        schema = {
            "oneOf": [
                {"required": ["x", "y"]},
                {"required": ["x", "z"]},
            ],
        }
        assert collect_all_required(schema) == {"x", "y", "z"}

    def test_mixed_top_and_branch(self):
        schema = {
            "required": ["common"],
            "oneOf": [
                {"required": ["branch_a"]},
            ],
        }
        assert collect_all_required(schema) == {"common", "branch_a"}


# ── collect_all_upstream ───────────────────────────────────────


class TestCollectAllUpstream:
    def test_top_level_only(self):
        schema = {"upstream": ["camera_ids"]}
        assert collect_all_upstream(schema) == {"camera_ids"}

    def test_empty(self):
        assert collect_all_upstream({}) == set()
        assert collect_all_upstream({"upstream": []}) == set()

    def test_branch_upstream(self):
        schema = {
            "oneOf": [
                {"upstream": ["camera_ids"]},
                {"upstream": ["area_code"]},
            ],
        }
        assert collect_all_upstream(schema) == {"camera_ids", "area_code"}

    def test_mixed_top_and_branch(self):
        schema = {
            "upstream": ["shared_field"],
            "oneOf": [
                {"upstream": ["branch_a"]},
                {"upstream": ["branch_b"]},
            ],
        }
        assert collect_all_upstream(schema) == {"shared_field", "branch_a", "branch_b"}

    def test_branches_without_upstream(self):
        schema = {
            "oneOf": [
                {"properties": {"x": {"type": "string"}}},
                {"properties": {"y": {"type": "integer"}}},
            ],
        }
        assert collect_all_upstream(schema) == set()
