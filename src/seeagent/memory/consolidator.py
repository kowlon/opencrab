"""
记忆整合器 - 批量整理对话历史

实现用户的想法:
1. 保存一整天的对话上下文
2. 空闲时段 (如凌晨) 自动整理
3. 归纳精华存入 MEMORY.md

参考:
- Claude-Mem Worker Service
- LangMem Background Manager
"""

import json
import logging
import os
import shutil
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from .extractor import MemoryExtractor
from .types import ConversationTurn, Memory, SessionSummary

logger = logging.getLogger(__name__)


class MemoryConsolidator:
    """记忆整合器 - 批量处理对话历史"""

    # 对话历史按月份分片 + 时间戳前缀：
    #   conversation_history/YYYYMM/{YYYYMMDDHHMMSS}__{session_id}.jsonl
    # 时间戳前缀使 IDE 字母序 == 时间序，最近的会话在列表底部。
    # _UNKNOWN_BUCKET 兜底：无法判定月份的遗留文件。
    _UNKNOWN_BUCKET = "unknown"
    _TS_PREFIX_LEN = 14  # YYYYMMDDHHMMSS

    def __init__(
        self,
        data_dir: Path,
        brain=None,
        extractor: MemoryExtractor | None = None,
    ):
        """
        Args:
            data_dir: 数据目录 (存放对话历史)
            brain: LLM 大脑实例
            extractor: 记忆提取器
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.brain = brain
        self.extractor = extractor or MemoryExtractor(brain)

        # 对话历史存储目录
        self.history_dir = self.data_dir / "conversation_history"
        self.history_dir.mkdir(exist_ok=True)

        # 已整理的会话
        self.summaries_file = self.data_dir / "session_summaries.json"

        # session_id → 已定位/创建的 jsonl 文件路径（进程内缓存，减少 glob 开销）
        self._file_cache: dict[str, Path] = {}

        # 首次启动迁移：扁平 → 月份分片
        self._migrate_flat_history_if_needed()
        # 第二次迁移：为缺失时间戳前缀的文件补前缀
        self._migrate_add_timestamp_prefix_if_needed()

    # ==================== 分片工具 ====================

    def _month_from_session_id(self, session_id: str) -> str | None:
        """从 session_id 提取月份，识别 2 种格式：

        1. ``{channel}_{chat_id}_{YYYYMMDDHHMMSS}_{uuid8}`` — parts[2] 前 6 位
           （``Session.create()`` 规范格式）
        2. ``{YYYYMMDD}_{HHMMSS}_{uuid8}`` — parts[0] 前 6 位
           （``core/agent.py:849`` Agent 通用启动格式）

        识别不出（如 seecrab 的 ``seecrab__seecrab_<hex>__seecrab_user``）返回 None。
        """
        parts = session_id.split("_")
        # Pattern 1: parts[2] 是 YYYYMMDDHHMMSS
        if len(parts) >= 3:
            ts = parts[2]
            if len(ts) >= 6 and ts[:6].isdigit() and 190000 <= int(ts[:6]) <= 999912:
                return ts[:6]
        # Pattern 2: parts[0] 是 YYYYMMDD
        if parts:
            head = parts[0]
            if len(head) == 8 and head.isdigit() and 19000101 <= int(head) <= 99991231:
                return head[:6]
        return None

    def _session_id_from_stem(self, stem: str) -> str | None:
        """从新格式文件名 stem（``{14digits}__{session_id}``）提取 session_id。

        不匹配新格式（遗留文件）返回 None。
        """
        if "__" not in stem:
            return None
        prefix, _, rest = stem.partition("__")
        if len(prefix) == self._TS_PREFIX_LEN and prefix.isdigit():
            return rest
        return None

    def _find_session_file(self, session_id: str) -> Path | None:
        """查找已存在的 session 文件（含时间戳前缀的新格式）。

        命中顺序：
        1. 进程内缓存（若文件仍存在）
        2. glob 扫描所有月份子目录，精确匹配 ``{14digits}__{session_id}.jsonl``
        3. 兼容遗留：无前缀的 ``{session_id}.jsonl``（老文件可能未迁移或迁移期间）

        未找到返回 None。
        """
        cached = self._file_cache.get(session_id)
        if cached is not None:
            if cached.exists():
                return cached
            del self._file_cache[session_id]

        # 新格式：{ts}__{session_id}.jsonl
        suffix = f"__{session_id}.jsonl"
        for candidate in self.history_dir.glob(f"*/*{suffix}"):
            if self._session_id_from_stem(candidate.stem) == session_id:
                self._file_cache[session_id] = candidate
                return candidate

        # 兼容遗留：{session_id}.jsonl（无前缀）
        for candidate in self.history_dir.glob(f"*/{session_id}.jsonl"):
            self._file_cache[session_id] = candidate
            return candidate

        return None

    def _new_file_for_session(self, session_id: str) -> Path:
        """为 session 创建新的带时间戳前缀的文件路径（首次写入用）。

        - 月份子目录：优先用 session_id 解析出的月份；否则用当前月份
        - 时间戳前缀：``datetime.now()`` 的 ``YYYYMMDDHHMMSS``
        - 目录自动创建；文件不预先 touch，由调用方 append 时创建
        """
        month = self._month_from_session_id(session_id) or datetime.now().strftime("%Y%m")
        month_dir = self.history_dir / month
        month_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        return month_dir / f"{ts}__{session_id}.jsonl"

    def _iter_history_files(self, since: datetime | None = None) -> Iterator[Path]:
        """遍历所有对话 jsonl 文件。

        Args:
            since: 若提供，只遍历 ``YYYYMM >= since.YYYYMM`` 的月份子目录；
                   ``unknown/`` 始终纳入（保守）。None 则遍历全部。
        """
        if since is None:
            yield from self.history_dir.glob("*/*.jsonl")
            return
        cutoff_month = since.strftime("%Y%m")
        for sub in self.history_dir.iterdir():
            if not sub.is_dir():
                continue
            if sub.name == self._UNKNOWN_BUCKET or sub.name >= cutoff_month:
                yield from sub.glob("*.jsonl")

    def save_conversation_turn(
        self,
        session_id: str,
        turn: ConversationTurn,
    ) -> None:
        """
        保存对话轮次 (实时保存)

        每个会话一个文件，追加写入；首次写入创建 ``{YYYYMMDDHHMMSS}__{session_id}.jsonl``
        （时间戳 = 首次写入时刻），后续 append 沿用同一文件。
        """
        session_file = self._find_session_file(session_id)
        if session_file is None:
            session_file = self._new_file_for_session(session_id)
            self._file_cache[session_id] = session_file

        with open(session_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(turn.to_dict(), ensure_ascii=False) + "\n")

    def load_session_history(self, session_id: str) -> list[ConversationTurn]:
        """加载会话历史（glob 所有月份子目录，命中第一个匹配文件）"""
        session_file = self._find_session_file(session_id)
        if session_file is None:
            return []

        turns = []
        with open(session_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    turn = ConversationTurn(
                        role=data["role"],
                        content=data["content"],
                        timestamp=datetime.fromisoformat(data["timestamp"]),
                        tool_calls=data.get("tool_calls", []),
                        tool_results=data.get("tool_results", []),
                    )
                    turns.append(turn)

        return turns

    def get_today_sessions(self) -> list[str]:
        """获取今天的所有会话 ID"""
        today = datetime.now().date()
        # 只扫当月子目录（含 unknown/）即可——今天的文件不可能出现在更早的月份
        since = datetime(today.year, today.month, 1)
        sessions = []

        for file in self._iter_history_files(since=since):
            mtime = datetime.fromtimestamp(file.stat().st_mtime)
            if mtime.date() == today:
                sessions.append(self._session_id_from_stem(file.stem) or file.stem)

        return sessions

    def get_unprocessed_sessions(self) -> list[str]:
        """获取未处理的会话"""
        # 加载已处理的会话
        processed = set()
        if self.summaries_file.exists():
            with open(self.summaries_file, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        summary = json.loads(line)
                        processed.add(summary["session_id"])

        # 找出未处理的
        unprocessed = []
        for file in self._iter_history_files():
            sid = self._session_id_from_stem(file.stem) or file.stem
            if sid not in processed:
                unprocessed.append(sid)

        return unprocessed

    async def consolidate_session(
        self,
        session_id: str,
    ) -> tuple[SessionSummary, list[Memory]]:
        """
        整理单个会话

        1. 加载对话历史
        2. 生成会话摘要
        3. 提取记忆
        """
        turns = self.load_session_history(session_id)

        if not turns:
            return None, []

        # 生成会话摘要
        summary = await self._generate_summary(session_id, turns)

        # 提取记忆
        memories = []

        # 基于规则提取
        for turn in turns:
            extracted = self.extractor.extract_from_turn(turn)
            memories.extend(extracted)

        # 使用 LLM 高级提取
        if self.brain:
            llm_memories = await self.extractor.extract_with_llm(
                turns, context=f"会话摘要: {summary.task_description}"
            )
            memories.extend(llm_memories)

        # 去重
        memories = self.extractor.deduplicate(memories, [])

        # 更新摘要中的记忆 ID
        summary.memories_created = [m.id for m in memories]

        # 保存摘要
        self._save_summary(summary)

        return summary, memories

    async def consolidate_all_unprocessed(self) -> tuple[list[SessionSummary], list[Memory]]:
        """
        整理所有未处理的会话

        适合在空闲时段 (如凌晨) 批量执行
        """
        unprocessed = self.get_unprocessed_sessions()

        all_summaries = []
        all_memories = []

        for session_id in unprocessed:
            try:
                summary, memories = await self.consolidate_session(session_id)
                if summary:
                    all_summaries.append(summary)
                    all_memories.extend(memories)
                    logger.info(f"Consolidated session {session_id}: {len(memories)} memories")
            except Exception as e:
                logger.error(f"Failed to consolidate session {session_id}: {e}")

        return all_summaries, all_memories

    async def _generate_summary(
        self,
        session_id: str,
        turns: list[ConversationTurn],
    ) -> SessionSummary:
        """使用 LLM 生成会话摘要"""

        start_time = turns[0].timestamp if turns else datetime.now()
        end_time = turns[-1].timestamp if turns else datetime.now()

        # 简单摘要 (不用 LLM)
        if not self.brain or len(turns) < 3:
            # 从用户消息提取任务描述
            user_messages = [t.content for t in turns if t.role == "user"]
            task_desc = user_messages[0][:200] if user_messages else "Unknown task"

            return SessionSummary(
                session_id=session_id,
                start_time=start_time,
                end_time=end_time,
                task_description=task_desc,
                outcome="completed",
            )

        # 使用 LLM 生成详细摘要
        from seeagent.core.tool_executor import smart_truncate as _st
        conv_text = "\n".join(
            [
                f"[{turn.role}]: {_st(turn.content or '', 600, save_full=False, label='consol_conv')[0]}"
                for turn in turns[-30:]
            ]
        )

        prompt = f"""总结以下对话会话:

{conv_text}

请提供:
1. task_description: 用户的主要任务是什么 (一句话)
2. outcome: 任务结果 (success/partial/failed)
3. key_actions: 关键操作 (最多5个)
4. learnings: 值得记住的经验 (最多3个)
5. errors: 遇到的错误 (如果有)

用 JSON 格式输出。
"""

        try:
            response = await self.brain.think(
                prompt, system="你是一个会话分析专家，擅长提取关键信息。只输出 JSON，不要其他内容。"
            )

            # 解析 JSON
            import re

            json_match = re.search(r"\{.*\}", response.content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return SessionSummary(
                    session_id=session_id,
                    start_time=start_time,
                    end_time=end_time,
                    task_description=data.get("task_description", ""),
                    outcome=data.get("outcome", "completed"),
                    key_actions=data.get("key_actions", []),
                    learnings=data.get("learnings", []),
                    errors_encountered=data.get("errors", []),
                )
        except Exception as e:
            logger.error(f"LLM summary generation failed: {e}")

        # 回退到简单摘要
        user_messages = [t.content for t in turns if t.role == "user"]
        return SessionSummary(
            session_id=session_id,
            start_time=start_time,
            end_time=end_time,
            task_description=user_messages[0][:200] if user_messages else "Unknown",
            outcome="completed",
        )

    def _save_summary(self, summary: SessionSummary) -> None:
        """保存会话摘要"""
        with open(self.summaries_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary.to_dict(), ensure_ascii=False) + "\n")

    def get_recent_summaries(self, days: int = 7) -> list[SessionSummary]:
        """获取最近N天的会话摘要"""
        if not self.summaries_file.exists():
            return []

        cutoff = datetime.now() - timedelta(days=days)
        summaries = []

        with open(self.summaries_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    end_time = datetime.fromisoformat(data["end_time"])
                    if end_time > cutoff:
                        summaries.append(
                            SessionSummary(
                                session_id=data["session_id"],
                                start_time=datetime.fromisoformat(data["start_time"]),
                                end_time=end_time,
                                task_description=data.get("task_description", ""),
                                outcome=data.get("outcome", ""),
                                key_actions=data.get("key_actions", []),
                                learnings=data.get("learnings", []),
                                errors_encountered=data.get("errors_encountered", []),
                                memories_created=data.get("memories_created", []),
                            )
                        )

        return summaries

    def cleanup_old_history(self, days: int = 30) -> int:
        """
        清理旧的对话历史文件（按天数）

        保留摘要和记忆，删除原始对话
        """
        cutoff = datetime.now() - timedelta(days=days)
        deleted = 0

        for file in self._iter_history_files():
            mtime = datetime.fromtimestamp(file.stat().st_mtime)
            if mtime < cutoff:
                file.unlink()
                deleted += 1
                logger.info(f"Deleted old history file: {file.name}")

        return deleted

    # ==================== 容量限制清理 ====================

    # 配置常量
    MAX_HISTORY_DAYS = 30  # 最多保留 30 天
    MAX_HISTORY_FILES = 1000  # 最多保留 1000 个文件
    MAX_HISTORY_SIZE_MB = 500  # 最多占用 500MB

    def cleanup_history(self) -> dict:
        """
        清理历史对话，防止磁盘爆炸

        策略（按优先级）:
        1. 删除超过 MAX_HISTORY_DAYS 天的文件
        2. 如果文件数超过 MAX_HISTORY_FILES，删除最旧的
        3. 如果总大小超过 MAX_HISTORY_SIZE_MB，删除最旧的

        Returns:
            清理统计 {"by_age": n, "by_count": n, "by_size": n}
        """
        deleted = {"by_age": 0, "by_count": 0, "by_size": 0}

        # 1. 按天数清理
        deleted["by_age"] = self.cleanup_old_history(days=self.MAX_HISTORY_DAYS)

        # 获取所有历史文件，按修改时间排序（最旧的在前）
        files = sorted(self._iter_history_files(), key=lambda f: f.stat().st_mtime)

        # 2. 按文件数清理
        if len(files) > self.MAX_HISTORY_FILES:
            to_delete = files[: len(files) - self.MAX_HISTORY_FILES]
            for f in to_delete:
                try:
                    f.unlink()
                    deleted["by_count"] += 1
                    logger.debug(f"Deleted history file (by count): {f.name}")
                except Exception as e:
                    logger.error(f"Failed to delete {f.name}: {e}")

            # 更新文件列表
            files = files[len(to_delete) :]

        # 3. 按大小清理
        max_size = self.MAX_HISTORY_SIZE_MB * 1024 * 1024
        total_size = sum(f.stat().st_size for f in files)

        while total_size > max_size and files:
            f = files.pop(0)
            try:
                file_size = f.stat().st_size
                f.unlink()
                total_size -= file_size
                deleted["by_size"] += 1
                logger.debug(f"Deleted history file (by size): {f.name}")
            except Exception as e:
                logger.error(f"Failed to delete {f.name}: {e}")

        total_deleted = sum(deleted.values())
        if total_deleted > 0:
            logger.info(f"History cleanup completed: {deleted}")

        return deleted

    def get_history_stats(self) -> dict:
        """
        获取历史对话统计信息

        Returns:
            统计信息字典
        """
        files = list(self._iter_history_files())
        total_size = sum(f.stat().st_size for f in files)

        return {
            "file_count": len(files),
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "max_files": self.MAX_HISTORY_FILES,
            "max_size_mb": self.MAX_HISTORY_SIZE_MB,
            "max_days": self.MAX_HISTORY_DAYS,
        }

    # ==================== 扁平 → 月份分片迁移 ====================

    def _write_sentinel(self, path: Path, count: int) -> None:
        """原子写入迁移哨兵文件。"""
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {"migrated_at": datetime.now().isoformat(), "count": count},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def _migrate_flat_history_if_needed(self) -> None:
        """首次启动时把扁平布局的 jsonl 文件按月归档到子目录。

        哨兵文件: ``data_dir/.history_layout_v2``（放父目录防止用户清空 history_dir 时误删）

        幂等策略:
          - 哨兵存在 → 直接返回
          - 扁平层无 jsonl 且哨兵不存在 → 写哨兵，标记迁移完成（count=0）
          - 有扁平文件 → 按 session_id 识别月份，失败则用 mtime 兜底，最后落 unknown/
          - 仅当全部成功才写哨兵；否则下次启动会继续重试剩余文件
        """
        sentinel = self.data_dir / ".history_layout_v2"
        if sentinel.exists():
            return

        flat_files = [
            p for p in self.history_dir.iterdir()
            if p.is_file() and p.suffix == ".jsonl"
        ]
        if not flat_files:
            try:
                self._write_sentinel(sentinel, 0)
            except Exception as e:
                logger.warning(f"Failed to write history migration sentinel: {e}")
            return

        moved, failed = 0, 0
        now = datetime.now()

        for src in flat_files:
            try:
                month = self._month_from_session_id(src.stem)
                if not month:
                    # mtime 兜底
                    try:
                        mtime = datetime.fromtimestamp(src.stat().st_mtime)
                    except OSError:
                        mtime = now
                    if mtime.year < 2000 or mtime > now + timedelta(days=1):
                        month = self._UNKNOWN_BUCKET
                    else:
                        month = mtime.strftime("%Y%m")

                dst_dir = self.history_dir / month
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst = dst_dir / src.name

                if dst.exists():
                    # 目标已存在：若大小一致视为同份数据，直接删源（幂等）
                    if dst.stat().st_size == src.stat().st_size:
                        src.unlink()
                        moved += 1
                        continue
                    # 否则写成 conflict 副本，避免覆盖
                    dst = dst_dir / f"{src.stem}.conflict-{int(time.time())}.jsonl"

                try:
                    src.rename(dst)
                except OSError:
                    # 跨设备 EXDEV 等：copy + unlink fallback
                    shutil.copy2(src, dst)
                    src.unlink()
                moved += 1
            except Exception as e:
                logger.error(f"Failed to migrate history file {src.name}: {e}")
                failed += 1

        if failed == 0:
            try:
                self._write_sentinel(sentinel, moved)
            except Exception as e:
                logger.warning(f"Failed to write history migration sentinel: {e}")

        logger.info(
            f"conversation_history migration: moved={moved}, failed={failed}"
        )

    # ==================== 时间戳前缀补齐迁移 ====================

    def _read_first_timestamp(self, path: Path) -> datetime | None:
        """读第一行 JSON 的 ``timestamp`` 字段；失败返回 None。"""
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    ts_str = json.loads(line).get("timestamp", "")
                    if ts_str:
                        return datetime.fromisoformat(ts_str)
                    return None
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        return None

    def _migrate_add_timestamp_prefix_if_needed(self) -> None:
        """给缺失时间戳前缀的 jsonl 文件补 ``{YYYYMMDDHHMMSS}__`` 前缀。

        哨兵: ``data_dir/.history_layout_v3``

        时间戳来源优先级：第一行 JSON ``timestamp`` > 文件 mtime > 当前时间

        幂等：哨兵存在或无需要迁移文件时直接返回；失败文件下次启动重试。
        """
        sentinel = self.data_dir / ".history_layout_v3"
        if sentinel.exists():
            return

        legacy_files = [
            p for p in self.history_dir.glob("*/*.jsonl")
            if self._session_id_from_stem(p.stem) is None
        ]
        if not legacy_files:
            try:
                self._write_sentinel(sentinel, 0)
            except Exception as e:
                logger.warning(f"Failed to write history prefix sentinel: {e}")
            return

        renamed, failed = 0, 0
        for src in legacy_files:
            try:
                ts = self._read_first_timestamp(src)
                if ts is None:
                    try:
                        ts = datetime.fromtimestamp(src.stat().st_mtime)
                    except OSError:
                        ts = datetime.now()
                prefix = ts.strftime("%Y%m%d%H%M%S")

                dst = src.parent / f"{prefix}__{src.name}"
                if dst.exists():
                    # 极罕见：同秒同 session_id 已存在前缀版本 → conflict 后缀避免覆盖
                    dst = src.parent / f"{prefix}__{src.stem}.conflict-{int(time.time())}.jsonl"

                try:
                    src.rename(dst)
                except OSError:
                    shutil.copy2(src, dst)
                    src.unlink()
                renamed += 1
            except Exception as e:
                logger.error(f"Failed to add timestamp prefix to {src.name}: {e}")
                failed += 1

        if failed == 0:
            try:
                self._write_sentinel(sentinel, renamed)
            except Exception as e:
                logger.warning(f"Failed to write history prefix sentinel: {e}")

        logger.info(
            f"conversation_history prefix migration: renamed={renamed}, failed={failed}"
        )
