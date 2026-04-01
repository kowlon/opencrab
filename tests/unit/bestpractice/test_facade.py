"""BP facade integration tests — test the full initialization flow."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import seeagent.bestpractice.facade as facade
from seeagent.bestpractice.facade import (
    get_bp_engine,
    get_bp_handler,
    get_bp_state_manager,
    get_dynamic_prompt_section,
    get_static_prompt_section,
    init_bp_system,
    match_bp_from_message,
)
from seeagent.bestpractice.models import (
    BestPracticeConfig,
    SubtaskConfig,
    TriggerConfig,
    TriggerType,
)
from seeagent.bestpractice.engine import BPStateManager


@pytest.fixture(autouse=True)
def reset_facade():
    """Reset singleton state between tests."""
    facade._initialized = False
    facade._bp_engine = None
    facade._bp_handler = None
    facade._bp_state_manager = None
    facade._bp_config_loader = None
    facade._bp_context_bridge = None
    facade._bp_prompt_loader = None
    facade._bp_matcher = None
    facade._bp_prompt_builder = None
    yield
    facade._initialized = False
    facade._bp_engine = None
    facade._bp_handler = None
    facade._bp_state_manager = None
    facade._bp_config_loader = None
    facade._bp_context_bridge = None
    facade._bp_prompt_loader = None
    facade._bp_matcher = None
    facade._bp_prompt_builder = None


@pytest.fixture
def bp_base_path():
    return Path(__file__).parents[3] / "best_practice"


class TestFacadeInit:
    def test_init_with_real_configs(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        result = init_bp_system(search_paths=[bp_base_path])
        assert result is True

        engine = get_bp_engine()
        assert engine is not None

        handler = get_bp_handler()
        assert handler is not None
        assert len(handler.config_registry) >= 4

        state_mgr = get_bp_state_manager()
        assert state_mgr is not None

    def test_init_no_configs(self, tmp_path):
        result = init_bp_system(search_paths=[tmp_path])
        assert result is False
        assert get_bp_handler() is None

    def test_static_prompt_section(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        init_bp_system(search_paths=[bp_base_path])
        section = get_static_prompt_section()
        assert "最佳实践" in section
        assert "content-pipeline" in section or "内容创作" in section

    def test_dynamic_prompt_section_empty_session(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        init_bp_system(search_paths=[bp_base_path])
        section = get_dynamic_prompt_section("nonexistent-session")
        # No instances → empty
        assert section == ""

    def test_dynamic_prompt_with_instance(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        init_bp_system(search_paths=[bp_base_path])
        mgr = get_bp_state_manager()
        handler = get_bp_handler()

        # Create an instance
        config = list(handler.config_registry.values())[0]
        mgr.create_instance(config, "test-session", {"topic": "test"})

        section = get_dynamic_prompt_section("test-session")
        assert "active" in section
        assert "test-session" not in section  # session_id not in output

    def test_dynamic_prompt_waiting_input_routing(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        init_bp_system(search_paths=[bp_base_path])
        mgr = get_bp_state_manager()
        handler = get_bp_handler()
        config = list(handler.config_registry.values())[0]
        inst_id = mgr.create_instance(config, "test-session", {"topic": "test"})

        # Set first subtask to waiting_input
        from seeagent.bestpractice.models import SubtaskStatus
        first_subtask_id = config.subtasks[0].id
        mgr.update_subtask_status(inst_id, first_subtask_id, SubtaskStatus.WAITING_INPUT)

        section = get_dynamic_prompt_section("test-session")
        assert "bp_answer" in section or "等待" in section

    def test_dynamic_prompt_done_routing(self, bp_base_path):
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        init_bp_system(search_paths=[bp_base_path])
        mgr = get_bp_state_manager()
        handler = get_bp_handler()
        config = list(handler.config_registry.values())[0]
        inst_id = mgr.create_instance(config, "test-session", {"topic": "test"})

        from seeagent.bestpractice.models import SubtaskStatus
        first_subtask_id = config.subtasks[0].id
        mgr.update_subtask_status(inst_id, first_subtask_id, SubtaskStatus.DONE)
        mgr.advance_subtask(inst_id)

        section = get_dynamic_prompt_section("test-session")
        assert "bp_next" in section or "bp_edit_output" in section or "bp_cancel" in section


class TestMatchBPFromMessage:
    """Tests for match_bp_from_message() trigger matching."""

    @pytest.fixture
    def sample_bp_config(self):
        """A BestPracticeConfig with a CONTEXT trigger containing keywords."""
        return BestPracticeConfig(
            id="test-bp",
            name="Test Best Practice",
            description="A test BP for matching",
            subtasks=[
                SubtaskConfig(id="s1", name="Step One", agent_profile="default"),
                SubtaskConfig(id="s2", name="Step Two", agent_profile="default"),
            ],
            triggers=[
                TriggerConfig(
                    type=TriggerType.CONTEXT,
                    conditions=["write an article", "create content"],
                ),
            ],
        )

    @pytest.fixture
    def setup_facade_with_config(self, sample_bp_config):
        """Wire up facade globals with a mock config loader and real state manager."""
        from seeagent.bestpractice.prompt import BPMatcher
        from seeagent.bestpractice.prompt import PromptTemplateLoader

        mock_loader = MagicMock()
        mock_loader.configs = {sample_bp_config.id: sample_bp_config}

        state_mgr = BPStateManager()
        prompt_loader = PromptTemplateLoader()

        facade._initialized = True
        facade._bp_config_loader = mock_loader
        facade._bp_state_manager = state_mgr
        facade._bp_prompt_loader = prompt_loader
        facade._bp_matcher = BPMatcher(
            config_loader=mock_loader,
            state_manager=state_mgr,
            prompt_loader=prompt_loader,
        )
        return state_mgr

    def test_keyword_match_returns_metadata(
        self, sample_bp_config, setup_facade_with_config,
    ):
        result = match_bp_from_message(
            "I want to write an article about AI", "sess-1",
        )
        assert result is not None
        assert result["bp_id"] == "test-bp"
        assert result["bp_name"] == "Test Best Practice"
        assert result["description"] == "A test BP for matching"
        assert result["subtask_count"] == 2
        assert result["subtasks"] == [
            {"id": "s1", "name": "Step One"},
            {"id": "s2", "name": "Step Two"},
        ]

    def test_no_match_returns_none(self, setup_facade_with_config):
        result = match_bp_from_message("hello, how are you?", "sess-1")
        assert result is None

    def test_cooldown_respected(self, setup_facade_with_config):
        state_mgr = setup_facade_with_config
        state_mgr.set_cooldown("sess-1", turns=3)

        result = match_bp_from_message(
            "I want to write an article", "sess-1",
        )
        assert result is None

    def test_active_instance_skipped(
        self, sample_bp_config, setup_facade_with_config,
    ):
        state_mgr = setup_facade_with_config
        state_mgr.create_instance(sample_bp_config, "sess-1")

        result = match_bp_from_message(
            "I want to write an article", "sess-1",
        )
        assert result is None

    def test_not_initialized_triggers_init(self):
        """When _initialized is False and no BP dirs exist, returns None gracefully."""
        facade._initialized = False
        result = match_bp_from_message("write an article", "sess-1")
        assert result is None

    def test_suspended_instance_still_matched_in_keyword_matchTest(
        self, sample_bp_config, setup_facade_with_config,
    ):
        """Keyword match should still match bp_id even when a SUSPENDED instance exists.

        Suspended BPs are allowed to re-match so the offer card is shown for new starts.
        Resume intent is handled upstream by the route layer, not by the matcher.
        """
        state_mgr = setup_facade_with_config
        inst_id = state_mgr.create_instance(sample_bp_config, "sess-1")
        state_mgr.suspend(inst_id)

        result = match_bp_from_message(
            "I want to write an article", "sess-1",
        )
        assert result is not None
        assert result["bp_id"] == sample_bp_config.id

    def test_command_trigger_not_matched(self, setup_facade_with_config):
        """COMMAND triggers should not be matched by match_bp_from_message."""
        # Replace the config with one that only has a COMMAND trigger
        cmd_config = BestPracticeConfig(
            id="cmd-bp",
            name="Command BP",
            description="Triggered by command only",
            subtasks=[
                SubtaskConfig(id="c1", name="Cmd Step", agent_profile="default"),
            ],
            triggers=[
                TriggerConfig(type=TriggerType.COMMAND, pattern="/run-bp"),
            ],
        )
        facade._bp_config_loader.configs = {cmd_config.id: cmd_config}

        result = match_bp_from_message("/run-bp", "sess-1")
        assert result is None


class TestBuildBpSectionIncludesDynamic:
    """Regression test: prompt/builder._build_bp_section must include dynamic section.

    Previously _build_bp_section returned only the static section, so the agent
    never saw suspended instance IDs and couldn't call bp_switch_task.
    """

    def test_build_bp_section_includes_suspended_instancesTest(self, bp_base_path):
        """_build_bp_section(session_id) must contain suspended instance info."""
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        from seeagent.prompt.builder import _build_bp_section

        init_bp_system(search_paths=[bp_base_path])
        mgr = get_bp_state_manager()
        handler = get_bp_handler()
        config = list(handler.config_registry.values())[0]

        # Create an instance then suspend it
        inst_id = mgr.create_instance(config, "sess-with-suspended", {})
        from seeagent.bestpractice.models import BPStatus
        mgr._instances[inst_id].status = BPStatus.SUSPENDED

        section = _build_bp_section("sess-with-suspended")
        # Must contain both static and dynamic content
        assert "最佳实践" in section or config.name in section
        assert inst_id in section or "bp_switch_task" in section

    def test_build_bp_section_empty_session_id_omits_dynamicTest(self, bp_base_path):
        """_build_bp_section('') must return only static section (no per-session dynamic)."""
        if not bp_base_path.is_dir():
            pytest.skip("best_practice/ directory not found")

        from seeagent.prompt.builder import _build_bp_section

        init_bp_system(search_paths=[bp_base_path])
        section = _build_bp_section("")
        # Static section present
        assert section != ""
        # No dynamic per-instance content (target_instance_id=") for empty session_id
        assert 'target_instance_id="' not in section
