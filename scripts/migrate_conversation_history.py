#!/usr/bin/env python3
"""
对话历史按月份分片迁移脚本

把 data/memory/conversation_history/{session_id}.jsonl 归档为
data/memory/conversation_history/YYYYMM/{session_id}.jsonl。

使用方法:
    python scripts/migrate_conversation_history.py                      # 执行迁移
    python scripts/migrate_conversation_history.py --dry-run            # 仅打印计划
    python scripts/migrate_conversation_history.py --force              # 忽略哨兵重新扫
    python scripts/migrate_conversation_history.py --verify             # 迁完做 sha256 一致性校验
    python scripts/migrate_conversation_history.py --rollback           # 回滚到扁平布局
    python scripts/migrate_conversation_history.py --data-dir PATH      # 指定数据目录
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

SENTINEL_NAME = ".history_layout_v2"
UNKNOWN_BUCKET = "unknown"


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def month_from_session_id(session_id: str) -> str | None:
    parts = session_id.split("_")
    if len(parts) < 3:
        return None
    ts = parts[2]
    if len(ts) >= 6 and ts[:6].isdigit() and 190000 <= int(ts[:6]) <= 999912:
        return ts[:6]
    return None


def resolve_month(src: Path, now: datetime) -> str:
    """按 session_id 判断月份；识别不出来则用 mtime 兜底，异常则归 unknown/。"""
    month = month_from_session_id(src.stem)
    if month:
        return month
    try:
        mtime = datetime.fromtimestamp(src.stat().st_mtime)
    except OSError:
        return UNKNOWN_BUCKET
    if mtime.year < 2000 or mtime > now + timedelta(days=1):
        return UNKNOWN_BUCKET
    return mtime.strftime("%Y%m")


def acquire_lock(data_dir: Path):
    """用文件锁防止和运行中的 seeagent 服务同时操作。"""
    lock_path = data_dir / ".migrate.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # 锁文件需要在整个进程生命周期保持打开，故不用 with 管理
    lock_fd = open(lock_path, "w")  # noqa: SIM115
    try:
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as e:
                lock_fd.close()
                raise RuntimeError(
                    f"无法获取迁移锁 {lock_path} - 可能有另一个迁移进程或 seeagent 服务正在运行"
                ) from e
        else:
            import fcntl

            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                lock_fd.close()
                raise RuntimeError(
                    f"无法获取迁移锁 {lock_path} - 可能有另一个迁移进程或 seeagent 服务正在运行"
                ) from e
    except Exception:
        lock_fd.close()
        raise
    return lock_fd


def write_sentinel(sentinel: Path, count: int) -> None:
    tmp = sentinel.with_suffix(sentinel.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {"migrated_at": datetime.now().isoformat(), "count": count},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(tmp, sentinel)


def plan_migration(history_dir: Path, now: datetime) -> list[tuple[Path, Path]]:
    """返回 [(src, dst), ...]，只包含扁平层的 jsonl。"""
    flat = [p for p in history_dir.iterdir() if p.is_file() and p.suffix == ".jsonl"]
    plan = []
    for src in flat:
        month = resolve_month(src, now)
        dst = history_dir / month / src.name
        plan.append((src, dst))
    return plan


def do_migrate(
    history_dir: Path,
    sentinel: Path,
    dry_run: bool,
    verify: bool,
) -> tuple[int, int, list[dict]]:
    now = datetime.now()
    plan = plan_migration(history_dir, now)

    if not plan:
        print("[INFO] 扁平层无 .jsonl 文件需要迁移")
        if not dry_run and not sentinel.exists():
            write_sentinel(sentinel, 0)
            print(f"[INFO] 已写入哨兵 {sentinel}")
        return 0, 0, []

    # 按目标月份聚合打印
    bucket_counts: dict[str, int] = {}
    for _, dst in plan:
        bucket_counts[dst.parent.name] = bucket_counts.get(dst.parent.name, 0) + 1
    print(f"[PLAN] 共 {len(plan)} 个文件，按月份分布: {bucket_counts}")

    if dry_run:
        print("[DRY-RUN] 不实际移动任何文件")
        return len(plan), 0, [
            {"src": str(s), "dst": str(d), "action": "planned"} for s, d in plan
        ]

    moved = 0
    failed = 0
    report: list[dict] = []

    for src, dst in plan:
        entry = {"src": str(src), "dst": str(dst)}
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)

            src_sha = sha256_of(src) if verify else None
            src_size = src.stat().st_size

            if dst.exists():
                if dst.stat().st_size == src_size:
                    # 幂等：已有同大小目标视为同一份数据
                    src.unlink()
                    entry["action"] = "dedup-removed-src"
                    report.append(entry)
                    moved += 1
                    continue
                dst = dst.parent / f"{src.stem}.conflict-{int(time.time())}.jsonl"
                entry["dst"] = str(dst)
                entry["action"] = "conflict-suffixed"
            else:
                entry["action"] = "renamed"

            try:
                src.rename(dst)
            except OSError:
                # EXDEV 跨设备 fallback
                shutil.copy2(src, dst)
                src.unlink()
                entry["action"] = entry.get("action", "") + "-via-copy"

            if verify and src_sha is not None:
                dst_sha = sha256_of(dst)
                if dst_sha != src_sha:
                    entry["error"] = f"sha256 mismatch: {src_sha} vs {dst_sha}"
                    failed += 1
                    report.append(entry)
                    continue
                entry["sha256"] = src_sha

            moved += 1
            report.append(entry)
        except Exception as e:
            entry["error"] = str(e)
            failed += 1
            report.append(entry)
            print(f"[ERROR] 迁移失败 {src.name}: {e}")

    if failed == 0:
        write_sentinel(sentinel, moved)
        print(f"[OK] 已写入哨兵 {sentinel}")
    else:
        print(f"[WARN] {failed} 个文件迁移失败，未写入哨兵（下次运行会继续重试剩余文件）")

    return moved, failed, report


def do_rollback(history_dir: Path, sentinel: Path, dry_run: bool) -> tuple[int, int]:
    """把 YYYYMM/*.jsonl 搬回扁平层；unknown/ 保留人工检查。"""
    moved = 0
    failed = 0

    subdirs = [
        p for p in history_dir.iterdir()
        if p.is_dir() and p.name != UNKNOWN_BUCKET
    ]
    total = sum(1 for sub in subdirs for _ in sub.glob("*.jsonl"))
    print(f"[PLAN] 从 {len(subdirs)} 个月份目录回滚 {total} 个文件到扁平层")

    if dry_run:
        return total, 0

    for sub in subdirs:
        for src in list(sub.glob("*.jsonl")):
            dst = history_dir / src.name
            try:
                if dst.exists():
                    # 目标已有同名：加时间戳后缀避免覆盖
                    dst = history_dir / f"{src.stem}.rollback-{int(time.time())}.jsonl"
                try:
                    src.rename(dst)
                except OSError:
                    shutil.copy2(src, dst)
                    src.unlink()
                moved += 1
            except Exception as e:
                failed += 1
                print(f"[ERROR] 回滚失败 {src}: {e}")
        # 尝试删空目录
        try:
            sub.rmdir()
        except OSError:
            pass

    # 删哨兵
    if sentinel.exists():
        try:
            sentinel.unlink()
            print(f"[OK] 已删除哨兵 {sentinel}")
        except OSError as e:
            print(f"[WARN] 删除哨兵失败: {e}")

    return moved, failed


def write_report(report: list[dict], data_dir: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    path = data_dir / f"migration_report_{ts}.json"
    path.write_text(
        json.dumps(
            {"generated_at": datetime.now().isoformat(), "entries": report},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="对话历史按月份分片迁移",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/memory"),
        help="记忆数据目录（默认 data/memory）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印计划不动磁盘")
    parser.add_argument("--force", action="store_true", help="忽略哨兵强制重新扫描")
    parser.add_argument("--verify", action="store_true", help="校验 sha256 一致性")
    parser.add_argument("--rollback", action="store_true", help="回滚到扁平布局")
    args = parser.parse_args()

    data_dir: Path = args.data_dir.resolve()
    history_dir = data_dir / "conversation_history"
    sentinel = data_dir / SENTINEL_NAME

    if not history_dir.exists():
        print(f"[ERROR] 对话历史目录不存在: {history_dir}")
        return 1

    print(f"[INFO] data_dir={data_dir}")
    print(f"[INFO] history_dir={history_dir}")
    print(f"[INFO] sentinel={sentinel} exists={sentinel.exists()}")

    try:
        lock_fd = acquire_lock(data_dir) if not args.dry_run else None
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        return 2

    try:
        if args.rollback:
            moved, failed = do_rollback(history_dir, sentinel, args.dry_run)
            print(f"[DONE] 回滚: moved={moved}, failed={failed}")
            return 0 if failed == 0 else 3

        if sentinel.exists() and not args.force:
            print("[INFO] 哨兵已存在，跳过迁移（--force 忽略）")
            return 0

        if args.force and sentinel.exists() and not args.dry_run:
            sentinel.unlink()
            print("[INFO] --force：已删除哨兵")

        moved, failed, report = do_migrate(
            history_dir, sentinel, args.dry_run, args.verify
        )
        print(f"[DONE] 迁移: moved={moved}, failed={failed}")

        if report and not args.dry_run:
            report_path = write_report(report, data_dir)
            print(f"[INFO] 详细报告: {report_path}")

        return 0 if failed == 0 else 3
    finally:
        if lock_fd is not None:
            try:
                lock_fd.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
