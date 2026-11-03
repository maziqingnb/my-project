"""看板数据 + 前端自检脚本（每 30 分钟跑一次，抓回归 bug）。

检查项：
1. 各报告 JSON 能正常解析、结构完整；
2. latest.json 的「推荐」里不能有已结算（赛果已出）的场次——已完场应归历史账本；
3. history.json 必须有 by_sport（足球/篮球分口径命中率），且记录不能缺 sport 字段
   （缺 sport 会落进「?」桶，把足球命中率漏计）；
4. 每个推荐的必需字段齐全；
5. docs/index.html 的内联 JS 语法合法（用 node --check）。

任一检查失败打印 [FAIL] 并以非零退出，供 GitHub Actions 红叉告警。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_REPORTS = ROOT / "docs" / "reports"
INDEX_HTML = ROOT / "docs" / "index.html"

REC_FIELDS = ("competition", "home", "away", "kickoff", "market", "pick",
              "side", "line", "score", "confidence", "sport", "competition_class")

errors: list[str] = []


def _read_json(name: str) -> dict | list | None:
    path = DOCS_REPORTS / name
    if not path.exists():
        errors.append(f"{name}: 文件不存在")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        errors.append(f"{name}: JSON 解析失败 — {e}")
        return None


def check_latest() -> None:
    d = _read_json("latest.json")
    if not isinstance(d, dict):
        return
    recs = d.get("recommendations")
    if not isinstance(recs, list):
        errors.append("latest.json: 缺少 recommendations 列表")
        return
    total = d.get("total")
    if total != len(recs):
        errors.append(f"latest.json: total({total}) 与推荐条数({len(recs)}) 不一致")
    settled_in_panel = []
    missing = []
    for r in recs:
        if not isinstance(r, dict):
            continue
        if (r.get("result") or {}).get("settled"):
            settled_in_panel.append(f"{r.get('home')} vs {r.get('away')}")
        for f in REC_FIELDS:
            if f not in r:
                missing.append(f"{r.get('home', '?')}:{f}")
    if settled_in_panel:
        errors.append(
            f"latest.json: 推荐里混入已结算场次 {len(settled_in_panel)} 场（应只留未开赛/进行中），"
            f"如 {settled_in_panel[:3]}…")
    if missing:
        errors.append(f"latest.json: 推荐缺字段 {missing[:5]}…")


def check_history() -> None:
    d = _read_json("history.json")
    if not isinstance(d, dict):
        return
    records = d.get("records")
    if not isinstance(records, list):
        errors.append("history.json: 缺少 records 列表")
        return
    stats = d.get("stats") or {}
    if "by_sport" not in stats:
        errors.append("history.json: stats 缺少 by_sport（足球/篮球命中率会显示「—」）")
    else:
        keys = list((stats["by_sport"] or {}).keys())
        if "?" in keys:
            errors.append("history.json: by_sport 出现「?」桶，说明有记录缺 sport 字段")
        if "football" not in keys and "basketball" not in keys:
            errors.append(f"history.json: by_sport 只有 {keys}，缺少足球/篮球口径")
    no_sport = [r.get("fixture_key") for r in records if isinstance(r, dict) and not r.get("sport")]
    if no_sport:
        errors.append(f"history.json: {len(no_sport)} 条记录缺 sport 字段，如 {no_sport[:3]}…")


def check_review() -> None:
    d = _read_json("review.json")
    if isinstance(d, dict) and not isinstance(d.get("days"), list):
        errors.append("review.json: 缺少 days 列表")


def check_live() -> None:
    d = _read_json("live.json")
    if isinstance(d, dict) and not isinstance(d.get("matches"), list):
        errors.append("live.json: 缺少 matches 列表")


def check_schedule() -> None:
    d = _read_json("schedule.json")
    if isinstance(d, dict) and not isinstance(d.get("matches"), list):
        errors.append("schedule.json: 缺少 matches 列表")


def check_index_js() -> None:
    if not INDEX_HTML.exists():
        errors.append("docs/index.html: 文件不存在")
        return
    html = INDEX_HTML.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    if not scripts:
        errors.append("docs/index.html: 未找到内联 <script>")
        return
    js = "\n".join(scripts)
    node = subprocess.run(["node", "--version"], capture_output=True)
    if node.returncode != 0:
        return  # 环境无 node，跳过 JS 语法检查
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
        tmp = f.name
    try:
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
        if r.returncode != 0:
            errors.append(f"docs/index.html: JS 语法错误 — {r.stderr.strip()[:300]}")
    finally:
        Path(tmp).unlink(missing_ok=True)


def main() -> int:
    check_latest()
    check_history()
    check_review()
    check_live()
    check_schedule()
    check_index_js()

    if errors:
        print("[BUGCHECK] 发现如下问题：")
        for e in errors:
            print(f"  [FAIL] {e}")
        print(f"[BUGCHECK] 共 {len(errors)} 个问题。")
        return 1
    print("[BUGCHECK] 全部通过：看板数据与前端自检无异常。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
