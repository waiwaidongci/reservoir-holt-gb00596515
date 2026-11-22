import json
from datetime import date
from sqlalchemy import func, select
from src.config.database import Session
from src.constants.enums import Kind, Role, Status
from src.domain import scenarios
from src.domain.demo import demo_rows
from src.domain.spec import SPEC
from src.models.forecast import ForecastVersion, ScenarioEntry
from src.models.identity import User
from src.models.jobs import Run
from src.models.source import Dataset
from src.services import forecast
from src.services.datasets import save
from src.utils.exports import write_report
from src.utils.security import hash_password
from src.utils.time import now

_SCENARIO_ROWS = [
    # (scenario, weight, forecast_date, net_rise_m, revision)；偏少情景缺 11-25，演示只计入概率上界。
    ('偏多', 0.25, '2026-11-21', 0.06, 1), ('偏多', 0.25, '2026-11-22', 0.07, 1),
    ('偏多', 0.25, '2026-11-23', 0.05, 1), ('偏多', 0.25, '2026-11-24', 0.06, 1),
    ('偏多', 0.25, '2026-11-25', 0.04, 1), ('偏多', 0.25, '2026-11-26', 0.05, 1),
    ('持平', 0.5, '2026-11-21', 0.01, 1), ('持平', 0.5, '2026-11-22', 0.02, 1),
    ('持平', 0.5, '2026-11-23', 0.01, 1), ('持平', 0.5, '2026-11-24', 0.02, 1),
    ('持平', 0.5, '2026-11-25', 0.01, 1), ('持平', 0.5, '2026-11-26', 0.02, 1),
    ('偏少', 0.25, '2026-11-21', 0.0, 1), ('偏少', 0.25, '2026-11-22', -0.01, 1),
    ('偏少', 0.25, '2026-11-23', 0.01, 1), ('偏少', 0.25, '2026-11-24', 0.0, 1),
    ('偏少', 0.25, '2026-11-26', -0.01, 1),
]


def seed_forecast(db, dataset):
    if db.scalar(select(ForecastVersion.id).where(ForecastVersion.version_no == 1)):
        return False
    version = ForecastVersion(version_no=1, source='澄湾雨量站', parts=1, author='seed')
    db.add(version)
    db.flush()
    entries = []
    for scenario, weight, day, net_rise, revision in _SCENARIO_ROWS:
        row = {'scenario': scenario, 'weight': weight, 'forecast_date': day,
               'net_rise_m': net_rise, 'revision': revision}
        entries.append(row)
        db.add(ScenarioEntry(version_id=version.id, scenario=scenario, forecast_date=date.fromisoformat(day),
                             net_rise_m=net_rise, weight=weight, revision=revision))
    params = scenarios.normalize_params({})
    result = scenarios.compute(dataset.rows, entries, params)
    job = Run(dataset_id=dataset.id, name='样本情景触线概率', kind=Kind.SCENARIO, version_id=version.id,
              status=Status.COMPLETE, params=params, result=result, attempt=1,
              author='seed', finished_at=now())
    db.add(job)
    db.flush()
    job.export_file = write_report(job.id, 1, result)
    forecast.publish(db, job, result)
    return True


def seed():
    with Session.begin() as db:
        for role in Role:
            if not db.get(User, role.value):
                db.add(User(username=role.value, role=role, password_hash=hash_password(f'{role.value}-demo-2611')))
        item, created = save(db, SPEC['default_dataset'], SPEC['sample_name'], demo_rows(), 'seed')
        forecast_seeded = seed_forecast(db, item)
        return {'users': db.scalar(select(func.count()).select_from(User)),
                'datasets': db.scalar(select(func.count()).select_from(Dataset)),
                'seed_dataset_id': item.id, 'row_count': item.row_count, 'checksum': item.checksum,
                'created_dataset': created, 'forecast_seeded': forecast_seeded}


if __name__ == '__main__':
    print(json.dumps(seed(), ensure_ascii=False))
