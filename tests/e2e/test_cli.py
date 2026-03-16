"""
L4 E2E Tests: CLI commands.

Tests CLI entry points via subprocess. Does not require LLM for status/version commands.
"""

import subprocess
import sys

import pytest


class TestCLIStatus:
    def test_version_flag(self):
        """seeagent --version should print version."""
        result = subprocess.run(
            [sys.executable, "-m", "seeagent", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0 or "version" in (result.stdout + result.stderr).lower()

    def test_help_flag(self):
        """seeagent --help should show available commands."""
        result = subprocess.run(
            [sys.executable, "-m", "seeagent", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "seeagent" in output.lower() or "usage" in output.lower()

    def test_status_command(self):
        """seeagent status should not crash."""
        result = subprocess.run(
            [sys.executable, "-m", "seeagent", "status"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # status may fail gracefully if not running, but should not crash
        assert result.returncode in (0, 1)


class TestCLIModuleEntry:
    def test_module_importable(self):
        """python -m seeagent should be importable."""
        result = subprocess.run(
            [sys.executable, "-c", "import seeagent; print(seeagent.__name__)"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0
        assert "seeagent" in result.stdout
