"""SQLite 存储层：results + odds_snapshots。

取代原来的 JSON 文件存储（data/results/*.json、data/odds_history/*.json）作为
**运行时**数据源：读盘更快、天然去重（UNIQUE 约束 + ON CONFLICT 合并盘口线）、
也便于后续做按联赛/日期聚合查询（P2 回测、P3 前端取数）。

git 持久化仍走 JSON：每个同步/分析工作流在收尾时把 SQLite 导出回 JSON（见
export_results / export_odds），diff 小、可审计，GitHub Actions 依然以 JSON 为
跨运行状态。SQLite 文件 data/app.db* 不入库（.gitignore），每次 Actions 运行从
committed JSON 重建（migrate_if_empty），本地开发则常驻复用。

DB 文件放在 DATA_DIR/app.db，因此 demo 模式 set_data_root 重定向后自动落到
临时目录、与真实数据隔离（和原 JSON 目录行为一致）。
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict

from . import config as C
from .models import Result, OddsSnapshot, norm_team

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    competition TEXT NOT NULL,
    cls         TEXT NOT NULL,
    home        TEXT NOT NULL,
    away        TEXT NOT NULL,
    home_norm   TEXT NOT NULL,
    away_norm   TEXT NOT NULL,
    home_goals  INTEGER NOT NULL,
    away_goals  INTEGER NOT NULL,
    date        TEXT NOT NULL,
    handicap_line REAL,
    total_line     REAL,
    UNIQUE (competition, home_norm, away_norm, date)
);
CREATE INDEX IF NOT EXISTS idx_results_comp ON results(competition, date);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_key TEXT NOT NULL,
    competition TEXT NOT NULL,
    home        TEXT NOT NULL,
    away        TEXT NOT NULL,
    kickoff     TEXT,
    bookmaker   TEXT,
    ts          TEXT NOT NULL,
    handicap_line  REAL,
    handicap_home  REAL,
    handicap_away  REAL,
    total_line     REAL,
    over           REAL,
    under          REAL
);
CREATE INDEX IF NOT EXISTS idx_odds_fk ON odds_snapshots(fixture_key, ts);
"""


def db_path():
    return C.DATA_DIR / "app.db"


def connect(path=None):
    """打开连接并确保 schema 存在；若从旧 JSON 首次迁移则一并灌入。

    WAL + busy_timeout 让「并发抓取、单线程写库」的场景下写入不被轻易打断。
    """
    conn = sqlite3.connect(str(path or db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    migrate_if_empty(conn)
    return conn


# ------------------------------------------------------------------ 写入

def upsert_results(conn, results: list[Result]) -> int:
    """批量写入/合并赛果。返回实际写入行数。

    去重键 (competition, home_norm, away_norm, date)。冲突时：
    - 比分/口径以新值为准（同场比赛两个源比分应一致）；
    - handicap_line / total_line 用 COALESCE 保留非空值——win007 完场带历史盘口线、
      7m 全季没有盘口线，无论谁先谁后都能保住 win007 的那条线。
    """
    rows = [
        (r.competition, r.cls, r.home, r.away,
         norm_team(r.home), norm_team(r.away),
         r.home_goals, r.away_goals, (r.date or "")[:10],
         r.handicap_line, r.total_line)
        for r in results
    ]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO results (competition, cls, home, away, home_norm, away_norm,
                             home_goals, away_goals, date, handicap_line, total_line)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(competition, home_norm, away_norm, date) DO UPDATE SET
            cls=excluded.cls,
            home_goals=excluded.home_goals,
            away_goals=excluded.away_goals,
            handicap_line=COALESCE(excluded.handicap_line, results.handicap_line),
            total_line=COALESCE(excluded.total_line, results.total_line)
        """,
        rows,
    )
    return len(rows)


def append_odds(conn, snaps: list[OddsSnapshot]) -> int:
    """追加赔率快照（append-only，与旧 JSON 语义一致：每次同步都留痕）。"""
    rows = [
        (s.fixture_key, s.competition, s.home, s.away, s.kickoff, s.bookmaker, s.ts,
         s.handicap_line, s.handicap_home, s.handicap_away, s.total_line, s.over, s.under)
        for s in snaps
    ]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO odds_snapshots (fixture_key, competition, home, away, kickoff,
                                    bookmaker, ts, handicap_line, handicap_home,
                                    handicap_away, total_line, over, under)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    return len(rows)


# ------------------------------------------------------------------ 读取

def load_results(conn, competition: str) -> list[Result]:
    # 稳定排序：date 降序 + 队名，保证导出 JSON 每次字节级一致（不给 git 制造无谓 diff）
    cur = conn.execute(
        "SELECT * FROM results WHERE competition=? ORDER BY date DESC, home_norm, away_norm",
        (competition,))
    return [_result_from_row(r) for r in cur]


def load_odds(conn, competition: str | None = None) -> list[OddsSnapshot]:
    if competition is None:
        cur = conn.execute("SELECT * FROM odds_snapshots ORDER BY ts")
    else:
        cur = conn.execute(
            "SELECT * FROM odds_snapshots WHERE competition=? ORDER BY ts", (competition,))
    return [_odds_from_row(r) for r in cur]


def count_results(conn, competition: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM results WHERE competition=?", (competition,)).fetchone()[0]


def _result_from_row(r) -> Result:
    return Result(competition=r["competition"], cls=r["cls"], home=r["home"],
                  away=r["away"], home_goals=r["home_goals"], away_goals=r["away_goals"],
                  date=r["date"], handicap_line=r["handicap_line"],
                  total_line=r["total_line"])


def _odds_from_row(r) -> OddsSnapshot:
    return OddsSnapshot(fixture_key=r["fixture_key"], competition=r["competition"],
                        home=r["home"], away=r["away"], kickoff=r["kickoff"],
                        bookmaker=r["bookmaker"], ts=r["ts"],
                        handicap_line=r["handicap_line"], handicap_home=r["handicap_home"],
                        handicap_away=r["handicap_away"], total_line=r["total_line"],
                        over=r["over"], under=r["under"])


# ------------------------------------------------------------------ 导出（git 持久化）

def export_results(conn, competitions) -> None:
    """把赛果导出回 data/results/{comp}.json（供 git 持久化与后续审计）。"""
    from .storage import write_json
    for comp in competitions:
        write_json(C.RESULTS_DIR / f"{comp.key}.json",
                   [asdict(r) for r in load_results(conn, comp.key)])


def export_odds(conn) -> None:
    """把赔率历史导出回 data/odds_history/*.json（保持旧的每场一文件布局）。

    内容不变时输出字节级一致，git 不会产生无谓 diff。
    """
    from .storage import write_json
    by_fixture: dict[str, list] = {}
    for s in load_odds(conn):
        by_fixture.setdefault(s.fixture_key, []).append(asdict(s))
    for fk, arr in by_fixture.items():
        safe = fk.replace("|", "_").replace("/", "_")
        write_json(C.ODDS_DIR / f"{safe}.json", arr)


# ------------------------------------------------------------------ 迁移

def migrate_if_empty(conn) -> None:
    """首次从旧 JSON 迁移到 SQLite（表为空且 JSON 存在时）。幂等。

    每个 Actions 运行都从 committed JSON 重建 DB：checkout 得到 JSON、无 app.db，
    这里自动灌入；本地开发跑过一次后表非空，自动跳过（只做两条 COUNT）。
    """
    if conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0:
        _migrate_results(conn)
    if conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0] == 0:
        _migrate_odds(conn)
    conn.commit()


def _migrate_results(conn) -> None:
    from .storage import read_json
    if not C.RESULTS_DIR.exists():
        return
    total = 0
    for f in sorted(C.RESULTS_DIR.glob("*.json")):
        raw = read_json(f, default=[]) or []
        objs = [Result(**r) for r in raw if isinstance(r, dict)]
        if objs:
            total += upsert_results(conn, objs)
    if total:
        print(f"[db] 已从 JSON 迁入 {total} 条赛果")


def _migrate_odds(conn) -> None:
    from .storage import read_json
    if not C.ODDS_DIR.exists():
        return
    total = 0
    for f in sorted(C.ODDS_DIR.glob("*.json")):
        raw = read_json(f, default=[]) or []
        objs = [OddsSnapshot(**r) for r in raw if isinstance(r, dict)]
        if objs:
            total += append_odds(conn, objs)
    if total:
        print(f"[db] 已从 JSON 迁入 {total} 条赔率快照")
