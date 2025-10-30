# coding: utf-8
"""
FastAPI-стенд для проверки гипотезы о снижении задержек p95 при 5 rps
за счёт кэширования Redis ответов на повторяющиеся запросы.

Эндпоинт:
POST /ask  { "query": "текст вопроса" }

Поведение:
1) Нормализуем ключ запроса (нижний регистр + схлопывание пробелов).
2) Ищем ответ в кэше Redis. При попадании — отдаём мгновенно.
3) При промахе — имитируем «тяжёлую» обработку (условный RAG) с задержкой,
   плюс пытаемся сопоставить ответ из локальной базы Q&A (data/qa.jsonl).
4) Кладём результат в Redis на CACHE_TTL_SECONDS.

Переменные окружения:
REDIS_URL          (по умолчанию redis://localhost:6379/0)
CACHE_TTL_SECONDS  (по умолчанию 3600)
SIM_LATENCY_MS     (по умолчанию 600)  — «тяжесть» генерации без кэша
SIM_JITTER_MS      (по умолчанию 200)  — случайная добавка к задержке
EXACT_CACHE        (1/0, по умолчанию 1) — выключить кэш для контрольного прогона
"""

import os
import re
import json
import random
import time
import asyncio
from typing import Dict, Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from redis.asyncio import Redis

# -------- конфиг из окружения --------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
SIM_LATENCY_MS = int(os.getenv("SIM_LATENCY_MS", "600"))
SIM_JITTER_MS = int(os.getenv("SIM_JITTER_MS", "200"))
EXACT_CACHE = os.getenv("EXACT_CACHE", "1") == "1"  # включить/выключить кэш

# -------- утилиты --------
def normalize(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text

def now_ms() -> int:
    return int(time.time() * 1000)

# -------- загрузка локальной БД вопрос-ответ --------
QA_PATH = os.path.join(os.path.dirname(__file__), "data", "qa.jsonl")
_QA: Dict[str, Dict[str, Any]] = {}

def load_qa() -> None:
    global _QA
    _QA = {}
    if not os.path.exists(QA_PATH):
        return
    with open(QA_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                q = normalize(obj.get("q", ""))
                if q:
                    _QA[q] = obj
            except Exception:
                continue

# наивное сопоставление: точное совпадение, иначе — поиск по ключевым словам
def find_answer(query: str) -> Dict[str, Any]:
    nq = normalize(query)
    if nq in _QA:
        return {"answer": _QA[nq]["a"], "match": "точное совпадение"}
    # очень простой эвристический поиск: максимум пересечений по словам
    q_words = set(w for w in re.findall(r"\w+", nq) if len(w) > 2)
    best_score = 0
    best = None
    for qk, obj in _QA.items():
        words = set(w for w in re.findall(r"\w+", qk) if len(w) > 2)
        score = len(q_words & words)
        if score > best_score:
            best_score = score
            best = obj
    if best and best_score > 0:
        return {"answer": best["a"], "match": "по словам"}
    return {"answer": "Ответ не найден в базе. Рекомендуется уточнить запрос.", "match": "нет совпадения"}

# -------- приложение --------
app = FastAPI(title="RAG Cache Benchmark API")

class AskIn(BaseModel):
    query: str

@app.on_event("startup")
async def on_startup():
    load_qa()
    app.state.redis = Redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)

@app.on_event("shutdown")
async def on_shutdown():
    r: Redis = app.state.redis
    await r.close()

@app.get("/health")
async def health():
    return {"status": "ok", "redis": REDIS_URL}

@app.post("/ask")
async def ask(body: AskIn):
    t0 = now_ms()
    nq = normalize(body.query)
    cache_key = f"qa:{nq}"
    r: Redis = app.state.redis

    # 1) кэш
    if EXACT_CACHE:
        cached = await r.get(cache_key)
        if cached is not None:
            t1 = now_ms()
            return {
                "query": body.query,
                "answer": cached,
                "from_cache": True,
                "latency_ms": t1 - t0,
                "cache_key": cache_key,
                "retrieval": {"match": "кэш"}
            }

    # 2) имитация «тяжёлой» обработки (условный RAG)
    delay = SIM_LATENCY_MS + random.randint(0, SIM_JITTER_MS)
    await asyncio.sleep(delay / 1000.0)

    # 3) находим ответ в локальной базе (наивно)
    ans = find_answer(body.query)
    answer_text = ans["answer"]

    # 4) кладём в кэш
    if EXACT_CACHE:
        try:
            await r.setex(cache_key, CACHE_TTL_SECONDS, answer_text)
        except Exception:
            pass

    t1 = now_ms()
    return {
        "query": body.query,
        "answer": answer_text,
        "from_cache": False,
        "latency_ms": t1 - t0,
        "cache_key": cache_key,
        "retrieval": {"match": ans["match"]}
    }

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8088, reload=False)
