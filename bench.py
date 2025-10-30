# coding: utf-8
"""
Нагрузочный скрипт для /ask.
Цель: при 5 rps добиться p95 < 900 мс за счёт кэша.

Пример:
python bench.py --rps 5 --duration 120 --warmup 10 --repeat-ratio 0.7

Аргументы:
--host           адрес API (по умолчанию http://127.0.0.1:8088)
--rps            запросов в секунду (по умолчанию 5)
--duration       длительность теста, сек (по умолчанию 120)
--warmup         прогрев без подсчёта метрик, сек (по умолчанию 10)
--queries-file   путь к списку запросов (по умолчанию data/bench_queries.txt)
--repeat-ratio   доля повторяющихся запросов (0..1), по умолчанию 0.7
"""

import argparse
import asyncio
import random
import statistics
import time
from typing import List, Tuple

import httpx

def load_queries(path: str) -> List[str]:
    qs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s:
                qs.append(s)
    if not qs:
        raise RuntimeError("Файл с запросами пуст.")
    return qs

def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s)-1) * (p/100.0)
    f = int(k)
    c = min(f+1, len(s)-1)
    if f == c:
        return s[int(k)]
    d0 = s[f] * (c-k)
    d1 = s[c] * (k-f)
    return d0 + d1

async def worker(client: httpx.AsyncClient, host: str, query: str) -> Tuple[float, bool]:
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{host}/ask", json={"query": query}, timeout=30.0)
        r.raise_for_status()
        data = r.json()
        # измеряем «пользовательскую» задержку как круговой трип
        t1 = time.perf_counter()
        latency_ms = (t1 - t0) * 1000.0
        return latency_ms, bool(data.get("from_cache"))
    except Exception:
        t1 = time.perf_counter()
        return (t1 - t0) * 1000.0, False

async def run_bench(host: str, rps: float, duration: int, warmup: int, queries: List[str], repeat_ratio: float):
    popular_pool = queries[:max(1, min(20, len(queries)))]
    all_pool = queries

    async with httpx.AsyncClient(http2=False) as client:
        # прогрев
        t_end_warm = time.perf_counter() + warmup
        while time.perf_counter() < t_end_warm:
            query = random.choice(popular_pool if random.random() < repeat_ratio else all_pool)
            await worker(client, host, query)
            await asyncio.sleep(1.0 / max(rps, 0.1))

        # основной прогон
        latencies = []
        hits = 0
        total = 0
        t_end = time.perf_counter() + duration
        while time.perf_counter() < t_end:
            query = random.choice(popular_pool if random.random() < repeat_ratio else all_pool)
            task = asyncio.create_task(worker(client, host, query))
            latency_ms, hit = await task
            latencies.append(latency_ms)
            hits += 1 if hit else 0
            total += 1
            await asyncio.sleep(1.0 / max(rps, 0.1))

    if not latencies:
        print("Нет данных для метрик.")
        return

    p50 = percentile(latencies, 50)
    p95 = percentile(latencies, 95)
    p99 = percentile(latencies, 99)
    mean = statistics.fmean(latencies)
    hit_rate = (hits / total) * 100.0 if total else 0.0

    print("\n=== Результаты нагрузочного прогона ===")
    print(f"Запросов: {total}, cache hit-rate: {hit_rate:.1f}%")
    print(f"Задержка (мс): p50={p50:.0f}  p95={p95:.0f}  p99={p99:.0f}  mean={mean:.0f}")
    target = 900.0
    print(f"Цель p95 < {target:.0f} мс: {'OK' if p95 < target else 'НЕ ДОСТИГНУТО'}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8088")
    ap.add_argument("--rps", type=float, default=5.0)
    ap.add_argument("--duration", type=int, default=120)
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--queries-file", default="data/bench_queries.txt")
    ap.add_argument("--repeat-ratio", type=float, default=0.7)
    args = ap.parse_args()

    queries = load_queries(args.queries_file)
    asyncio.run(run_bench(args.host, args.rps, args.duration, args.warmup, queries, args.repeat_ratio))

if __name__ == "__main__":
    main()
