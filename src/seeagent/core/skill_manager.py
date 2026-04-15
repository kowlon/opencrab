"""
技能管理器

从 agent.py 提取的技能安装/加载/更新逻辑，负责:
- 加载已安装的技能
- 从 Git 仓库安装技能
- 从 URL 安装技能
- 技能工具描述更新
- 外部技能 allowlist 管理
"""

import contextlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)


class SkillManager:
    """
    技能管理器。

    管理 Agent Skills (SKILL.md 规范) 的加载、安装和更新。
    """

    def __init__(
        self,
        skill_registry: Any,
        skill_loader: Any,
        skill_catalog: Any,
        shell_tool: Any,
        on_skill_loaded: Any = None,
    ) -> None:
        """
        Args:
            skill_registry: SkillRegistry 实例
            skill_loader: SkillLoader 实例
            skill_catalog: SkillCatalog 实例
            shell_tool: ShellTool 实例（用于 git 操作）
            on_skill_loaded: 技能加载后的回调（用于同步 handler_registry 等）
        """
        self._registry = skill_registry
        self._loader = skill_loader
        self._catalog = skill_catalog
        self._shell_tool = shell_tool
        self._on_skill_loaded = on_skill_loaded

        # 缓存
        self._catalog_text: str = ""

    @property
    def catalog_text(self) -> str:
        """获取技能清单文本"""
        return self._catalog_text

    async def load_installed_skills(self) -> None:
        """
        加载已安装的技能。

        技能从以下目录加载:
        - skills/ (项目级别)
        - .cursor/skills/ (Cursor 兼容)
        """
        loaded = self._loader.load_all(settings.project_root)
        logger.info(f"Loaded {loaded} skills from standard directories")

        # 外部技能 allowlist 过滤（支持 DEFAULT_DISABLED_SKILLS 默认禁用）
        try:
            cfg_path = settings.project_root / "data" / "skills.json"
            external_allowlist: set[str] | None = None
            if cfg_path.exists():
                raw = cfg_path.read_text(encoding="utf-8")
                cfg = json.loads(raw) if raw.strip() else {}
                al = cfg.get("external_allowlist", None)
                if isinstance(al, list):
                    external_allowlist = {str(x).strip() for x in al if str(x).strip()}
            effective = self._loader.compute_effective_allowlist(external_allowlist)
            from seeagent.core.agent import _collect_preset_referenced_skills
            agent_skills = _collect_preset_referenced_skills()
            removed = self._loader.prune_external_by_allowlist(
                effective, agent_referenced_skills=agent_skills,
            )
            if removed:
                logger.info(f"External skills filtered: {removed} disabled")
        except Exception as e:
            logger.warning(f"Failed to apply skills allowlist: {e}")

        self._catalog_text = self._catalog.generate_catalog()
        logger.info(f"Generated skill catalog with {self._catalog.skill_count} skills")

    async def install_skill(
        self,
        source: str,
        name: str | None = None,
        subdir: str | None = None,
        extra_files: list[str] | None = None,
    ) -> str:
        """
        安装技能到当前工作区的技能目录。

        支持:
        1. Git 仓库 URL
        2. 单个 SKILL.md 文件 URL

        Args:
            source: Git 仓库 URL 或 SKILL.md 文件 URL
            name: 技能名称
            subdir: Git 仓库中技能所在的子目录
            extra_files: 额外文件 URL 列表

        Returns:
            安装结果消息
        """
        skills_dir = settings.skills_path
        skills_dir.mkdir(parents=True, exist_ok=True)

        if self._is_git_url(source):
            return await self._install_from_git(source, name, subdir, skills_dir)
        else:
            return await self._install_from_url(source, name, extra_files, skills_dir)

    def _check_group_conflict(self, skills_dir: Path, skill_name: str) -> str | None:
        """检测分组子目录（如 ``1seetime/``）中是否已存在同名技能。

        扁平层 ``skills/{name}/`` 的同名冲突由安装流程的 ``shutil.rmtree`` 主动覆盖，
        维持原行为；此处仅拒绝跨"扁平 vs 分组"产生的重复。

        Returns:
            冲突提示串（调用方作为错误结果返回）；无冲突返回 None。
        """
        from ..skills.loader import SkillLoader

        hits = SkillLoader.find_skill_dirs_by_name(skills_dir, skill_name)
        group_hits = [p for p in hits if p.parent != skills_dir]
        if not group_hits:
            return None

        paths = "\n".join(f"  - {p}" for p in group_hits)
        return (
            f"❌ 技能 '{skill_name}' 已存在于分组子目录：\n{paths}\n\n"
            f"继续安装会在 {skills_dir / skill_name} 再创建一份，"
            f"导致下次启动时重复注册并覆盖（产生 WARNING）。\n"
            f"请任选其一：\n"
            f"1. 若要更新分组下的现有技能：直接编辑 {group_hits[0]}，再 reload_skill('{skill_name}')。\n"
            f"2. 若确需替换：先删除分组下的目录，再重试安装。\n"
            f"3. 以不同名称安装：使用 name 参数指定新名。"
        )

    def update_shell_tool_description(self, tools: list[dict]) -> None:
        """动态更新 shell 工具描述，包含当前操作系统信息"""
        import platform

        if os.name == "nt":
            os_info = (
                f"Windows {platform.release()} "
                "(使用 PowerShell/cmd 命令，如: dir, type, tasklist, Get-Process, findstr)"
            )
        else:
            os_info = f"{platform.system()} (使用 bash 命令，如: ls, cat, ps aux, grep)"

        for tool in tools:
            if tool.get("name") == "run_shell":
                tool["description"] = (
                    f"执行Shell命令。当前操作系统: {os_info}。"
                    "注意：请使用当前操作系统支持的命令；如果命令连续失败，请尝试不同的命令或放弃该方法。"
                )
                tool["input_schema"]["properties"]["command"]["description"] = (
                    f"要执行的Shell命令（当前系统: {os.name}）"
                )
                break

    # ==================== 私有方法 ====================

    def _is_git_url(self, url: str) -> bool:
        """判断是否为 Git 仓库 URL"""
        git_patterns = [
            r"^git@",
            r"\.git$",
            r"^https?://github\.com/",
            r"^https?://gitlab\.com/",
            r"^https?://bitbucket\.org/",
            r"^https?://gitee\.com/",
        ]
        return any(re.search(pattern, url) for pattern in git_patterns)

    async def _install_from_git(
        self, git_url: str, name: str | None, subdir: str | None, skills_dir: Path
    ) -> str:
        """从 Git 仓库安装技能"""
        import shutil
        import tempfile

        temp_dir = None
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix="skill_install_"))
            result = await self._shell_tool.run(f'git clone --depth 1 "{git_url}" "{temp_dir}"')

            if not result.success:
                return f"❌ Git 克隆失败:\n{result.output}"

            search_dir = temp_dir / subdir if subdir else temp_dir
            skill_md_path = self._find_skill_md(search_dir)

            if not skill_md_path:
                possible = self._list_skill_candidates(temp_dir)
                hint = ""
                if possible:
                    hint = "\n\n可能的技能目录:\n" + "\n".join(f"- {p}" for p in possible[:5])
                return f"❌ 未找到 SKILL.md 文件{hint}"

            skill_source_dir = skill_md_path.parent
            skill_content = skill_md_path.read_text(encoding="utf-8")
            extracted_name = self._extract_skill_name(skill_content)
            skill_name = name or extracted_name or skill_source_dir.name
            skill_name = self._normalize_skill_name(skill_name)

            conflict = self._check_group_conflict(skills_dir, skill_name)
            if conflict:
                return conflict

            target_dir = skills_dir / skill_name
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(skill_source_dir, target_dir)
            self._ensure_skill_structure(target_dir)

            try:
                loaded = self._loader.load_skill(target_dir)
                if loaded:
                    self._catalog_text = self._catalog.generate_catalog()
                    if self._on_skill_loaded:
                        self._on_skill_loaded()
                    logger.info(f"Skill installed from git: {skill_name}")
            except Exception as e:
                logger.error(f"Failed to load installed skill: {e}")

            return (
                f"✅ 技能从 Git 安装成功！\n\n"
                f"**技能名称**: {skill_name}\n"
                f"**来源**: {git_url}\n"
                f"**安装路径**: {target_dir}\n\n"
                f"**目录结构**:\n```\n{skill_name}/\n{self._format_tree(target_dir)}\n```\n\n"
                f'技能已自动加载，可以使用 `get_skill_info("{skill_name}")` 查看详细指令。'
            )

        except Exception as e:
            logger.error(f"Failed to install skill from git: {e}")
            return f"❌ Git 安装失败: {str(e)}"
        finally:
            if temp_dir and temp_dir.exists():
                with contextlib.suppress(BaseException):
                    import shutil
                    shutil.rmtree(temp_dir)

    async def _install_from_url(
        self, url: str, name: str | None, extra_files: list[str] | None, skills_dir: Path
    ) -> str:
        """从 URL 安装技能"""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                skill_content = response.text

            extracted_name = self._extract_skill_name(skill_content)
            skill_name = name or extracted_name

            if not skill_name:
                from urllib.parse import urlparse
                path = urlparse(url).path
                skill_name = path.split("/")[-1].replace(".md", "").replace("skill", "").strip("-_")

            skill_name = self._normalize_skill_name(skill_name or "custom-skill")

            conflict = self._check_group_conflict(skills_dir, skill_name)
            if conflict:
                return conflict

            skill_dir = skills_dir / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")
            self._ensure_skill_structure(skill_dir)

            installed_files = ["SKILL.md"]

            if extra_files:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    for file_url in extra_files:
                        try:
                            from urllib.parse import urlparse
                            file_name = urlparse(file_url).path.split("/")[-1]
                            if not file_name:
                                continue
                            resp = await client.get(file_url)
                            resp.raise_for_status()
                            if file_name.endswith(".md"):
                                dest = skill_dir / "references" / file_name
                            elif file_name.endswith((".py", ".sh", ".js")):
                                dest = skill_dir / "scripts" / file_name
                            else:
                                dest = skill_dir / file_name
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_text(resp.text, encoding="utf-8")
                            installed_files.append(str(dest.relative_to(skill_dir)))
                        except Exception as e:
                            logger.warning(f"Failed to download {file_url}: {e}")

            try:
                loaded = self._loader.load_skill(skill_dir)
                if loaded:
                    self._catalog_text = self._catalog.generate_catalog()
                    if self._on_skill_loaded:
                        self._on_skill_loaded()
                    logger.info(f"Skill installed from URL: {skill_name}")
            except Exception as e:
                logger.error(f"Failed to load installed skill: {e}")

            return (
                f"✅ 技能安装成功！\n\n"
                f"**技能名称**: {skill_name}\n"
                f"**安装路径**: {skill_dir}\n\n"
                f"**安装文件**: {', '.join(installed_files)}\n\n"
                f'技能已自动加载，可以使用 `get_skill_info("{skill_name}")` 查看详细指令。'
            )

        except Exception as e:
            logger.error(f"Failed to install skill from URL: {e}")
            return f"❌ URL 安装失败: {str(e)}"

    def _extract_skill_name(self, content: str) -> str | None:
        """从 SKILL.md 内容提取技能名称"""
        try:
            import yaml
        except ImportError:
            return None
        match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if match:
            try:
                metadata = yaml.safe_load(match.group(1))
                return metadata.get("name")
            except Exception:
                pass
        return None

    def _normalize_skill_name(self, name: str) -> str:
        """标准化技能名称"""
        name = name.lower().replace("_", "-").replace(" ", "-")
        name = re.sub(r"[^a-z0-9-]", "", name)
        name = re.sub(r"-+", "-", name).strip("-")
        return name or "custom-skill"

    def _find_skill_md(self, search_dir: Path) -> Path | None:
        """在目录中查找 SKILL.md"""
        skill_md = search_dir / "SKILL.md"
        if skill_md.exists():
            return skill_md
        for path in search_dir.rglob("SKILL.md"):
            return path
        return None

    def _list_skill_candidates(self, base_dir: Path) -> list[str]:
        """列出可能包含技能的目录"""
        candidates = []
        for path in base_dir.rglob("*.md"):
            if path.name.lower() in ("skill.md", "readme.md"):
                rel_path = path.parent.relative_to(base_dir)
                if str(rel_path) != ".":
                    candidates.append(str(rel_path))
        return candidates

    def _ensure_skill_structure(self, skill_dir: Path) -> None:
        """确保技能目录有规范结构"""
        (skill_dir / "scripts").mkdir(exist_ok=True)
        (skill_dir / "references").mkdir(exist_ok=True)
        (skill_dir / "assets").mkdir(exist_ok=True)

    def _format_tree(self, directory: Path, prefix: str = "") -> str:
        """格式化目录树"""
        lines = []
        items = sorted(directory.iterdir(), key=lambda x: (x.is_file(), x.name))
        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{item.name}")
            if item.is_dir():
                extension = "    " if is_last else "│   "
                sub_tree = self._format_tree(item, prefix + extension)
                if sub_tree:
                    lines.append(sub_tree)
        return "\n".join(lines)
