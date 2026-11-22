import csv
import io
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from src.config.database import connection
from src.constants.enums import Kind, Role
from src.controllers.auth import allow, current
from src.domain import scenarios
from src.models.forecast import ForecastVersion, PublishedForecast, ScenarioEntry
from src.models.identity import User
from src.models.jobs import Run
from src.models.source import Dataset
from src.schemas.requests import PartUpload, ScenarioRunRequest
from src.services import forecast
from src.services.audit import record
from src.utils import views
from src.utils.time import iso
from src.utils.wakeup import wake

router = APIRouter(prefix='/forecast')


def _counts(db):
    rows = db.execute(select(ScenarioEntry.version_id,
                             func.count().label('entries'),
                             func.count(func.distinct(ScenarioEntry.scenario)).label('scenarios'))
                      .group_by(ScenarioEntry.version_id)).all()
    return {row.version_id: row for row in rows}


def _published_map(db):
    return set(db.scalars(select(PublishedForecast.version_id)))


@router.get('/template')
def template(actor: User = Depends(current)):
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=[field['key'] for field in scenarios.PART_FIELDS])
    writer.writeheader()
    writer.writerows([
        {'scenario': '偏多', 'weight': 0.25, 'forecast_date': '2026-11-21', 'net_rise_m': 0.06, 'revision': 1},
        {'scenario': '偏多', 'weight': 0.25, 'forecast_date': '2026-11-22', 'net_rise_m': 0.07, 'revision': 1},
        {'scenario': '持平', 'weight': 0.5, 'forecast_date': '2026-11-21', 'net_rise_m': 0.01, 'revision': 1},
    ])
    filename = quote('情景预报_模板.csv')
    return Response('\ufeff' + buffer.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{filename}"})


@router.post('/parts', status_code=201)
def receive(body: PartUpload, db: Session = Depends(connection),
            actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    rows = scenarios.parse_part_csv(body.csv_text)
    version, created, stats = forecast.upsert_part(db, body.version_no, body.source, rows, actor.username)
    db.commit()
    counts = _counts(db).get(version.id)
    return {**views.version(version, counts.entries if counts else 0, counts.scenarios if counts else 0),
            'created': created, **stats}


@router.get('/versions')
def versions(db: Session = Depends(connection), actor: User = Depends(current)):
    counts = _counts(db)
    published = _published_map(db)
    items = db.scalars(select(ForecastVersion).order_by(ForecastVersion.version_no.desc()).limit(100))
    return [views.version(item, counts[item.id].entries if item.id in counts else 0,
                          counts[item.id].scenarios if item.id in counts else 0,
                          item.id in published) for item in items]


@router.get('/versions/{version_id}')
def version_detail(version_id: int, db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.get(ForecastVersion, version_id)
    if not item:
        raise HTTPException(404, '找不到该预报版次')
    counts = _counts(db).get(item.id)
    return {**views.version(item, counts.entries if counts else 0, counts.scenarios if counts else 0,
                            item.id in _published_map(db)),
            'rows': forecast.entries_of(db, item.id)}


@router.post('/runs', status_code=202)
def start(body: ScenarioRunRequest, db: Session = Depends(connection),
          actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    if not db.get(Dataset, body.dataset_id):
        raise HTTPException(404, '找不到指定的晨检台账批次')
    if not db.get(ForecastVersion, body.version_id):
        raise HTTPException(404, '找不到指定的预报版次')
    item = Run(name=body.name, dataset_id=body.dataset_id, kind=Kind.SCENARIO, version_id=body.version_id,
               params=scenarios.normalize_params(body.params), author=actor.username)
    db.add(item)
    db.flush()
    record(db, actor.username, 'forecast.queued', item.id,
           dataset_id=item.dataset_id, version_id=item.version_id)
    db.commit()
    wake()
    return views.run(item, detail=True)


@router.get('/current')
def current_published(db: Session = Depends(connection), actor: User = Depends(current)):
    latest_no = db.scalar(select(func.max(ForecastVersion.version_no)))
    row = db.execute(select(PublishedForecast, ForecastVersion)
                     .join(ForecastVersion, PublishedForecast.version_id == ForecastVersion.id)
                     .order_by(ForecastVersion.version_no.desc()).limit(1)).first()
    published = views.published(row[0], row[1].version_no, row[1].source) if row else None
    return {'latest_version_no': latest_no, 'published': published,
            'newer_pending': bool(latest_no and (not published or latest_no > published['version_no']))}


@router.get('/published')
def published_list(db: Session = Depends(connection), actor: User = Depends(current)):
    rows = db.execute(select(PublishedForecast, ForecastVersion)
                      .join(ForecastVersion, PublishedForecast.version_id == ForecastVersion.id)
                      .order_by(ForecastVersion.version_no.desc()).limit(100)).all()
    return [{'version_no': version.version_no, 'source': version.source, 'run_id': item.run_id,
             'published_at': iso(item.published_at), 'headline': item.result['headline'],
             'interval': item.result['interval'], 'window': item.result['window']}
            for item, version in rows]


@router.get('/published/{version_no}')
def published_detail(version_no: int, db: Session = Depends(connection), actor: User = Depends(current)):
    row = db.execute(select(PublishedForecast, ForecastVersion)
                     .join(ForecastVersion, PublishedForecast.version_id == ForecastVersion.id)
                     .where(ForecastVersion.version_no == version_no)).first()
    if not row:
        raise HTTPException(404, '该版次没有已发布的触线概率')
    return views.published(row[0], row[1].version_no, row[1].source)
