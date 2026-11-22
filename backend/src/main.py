import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text
from scripts.seed_demo import seed
from src.config.database import Base, engine
from src.config.settings import SEED_DEMO
from src.domain.spec import SPEC
from src.middlewares import errors, headers, rate
from src.routes import distribution, forecast, identity, registry, runs
from src.utils.wakeup import broker

log = logging.getLogger(__name__)

# 002 雨量情景预报：在既有 runs 表上补列（新表由 create_all 建立）。
MIGRATIONS = (
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'trend'",
    "ALTER TABLE runs ADD COLUMN IF NOT EXISTS version_id INTEGER",
    "CREATE INDEX IF NOT EXISTS ix_runs_kind ON runs (kind)",
    "CREATE INDEX IF NOT EXISTS ix_runs_version_id ON runs (version_id)",
)


@asynccontextmanager
async def lifespan(_app):
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for statement in MIGRATIONS:
            connection.execute(text(statement))
    if SEED_DEMO:
        try:
            log.info('Seeded %s', seed())
        except Exception:
            log.exception('Demo seed failed')
    yield
    engine.dispose()


app = FastAPI(title=SPEC['title'], lifespan=lifespan)
errors.install(app)
headers.install(app)
rate.install(app)
for module in (identity, registry, runs, forecast):
    app.include_router(module.router)
if SPEC.get('scheduled'):
    app.include_router(distribution.router)


@app.get('/healthz', include_in_schema=False)
def health():
    with engine.connect() as connection:
        connection.execute(text('SELECT 1'))
    broker.ping()
    return {'status': 'ok'}
