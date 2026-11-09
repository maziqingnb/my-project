"""并发执行工具：用线程池并行化 I/O 密集的抓取。

`requests` 是阻塞 I/O，GIL 在 socket 读写期间释放，线程池即可拿到真实并发，
无需引入 aiohttp（也避免 Actions 上多装依赖）。用于把「逐联赛串行」「逐日串行」
的抓取并行化——这是整轮同步最耗时的地方（结果同步每联赛要串行抓 30 个日期分页，
20 个联赛就是 600 次串行请求）。

峰值并发由调用方传入的 max_workers 控制（默认见 config.sync.max_workers），
避免对 win007 等站点瞬时打太多请求被反爬。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

DEFAULT_WORKERS = 6


def pmap(fn, items, max_workers: int = DEFAULT_WORKERS):
    """对 items 并发执行 fn，保持输入顺序返回结果列表。

    fn 抛异常时这里不吞——由调用方按业务决定是否兜底（同步层已逐联赛 try/except）。
    """
    items = list(items)
    if not items:
        return []
    workers = max_workers or DEFAULT_WORKERS
    workers = max(1, min(workers, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))
