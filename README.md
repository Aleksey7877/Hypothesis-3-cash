# Стенд для проверки гипотезы №3 (кэширование p95 < 2мин при 5 rps)

## Быстрый старт
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -r requirements.txt
redis-server   # запусти локально Redis или укажи REDIS_URL

# Запусти API
python app.py   # слушает 0.0.0.0:8088

# Прогрев + бенч (5 rps, 120 сек, прогрев 10 сек, повторов 70%)
python bench.py --rps 5 --duration 120 --warmup 10 --repeat-ratio 0.7
```

## Переменные окружения
- `REDIS_URL` (по умолчанию `redis://localhost:6379/0`)
- `CACHE_TTL_SECONDS` (по умолчанию `3600`)
- `SIM_LATENCY_MS` (по умолчанию `600`) — «тяжесть» RAG без кэша
- `SIM_JITTER_MS` (по умолчанию `200`)
- `EXACT_CACHE=1` — включить/выключить кэш (для контрольного прогона поставить `0`)

## Что считать успехом
Цель: **p95 < 900 мс** при 5 rps на основном прогоне (после прогрева) за счёт высокой доли cache hit на повторяющихся вопросах.
