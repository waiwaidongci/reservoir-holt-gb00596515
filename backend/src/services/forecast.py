"""情景预报版次的接收、失效与发布。"""

from datetime import date
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from src.constants.enums import Kind, Status
from src.models.forecast import ForecastVersion, PublishedForecast, ScenarioEntry
from src.models.jobs import Run
from src.services.audit import record
from src.utils.time import now


def upsert_part(db, version_no, source, rows, actor):
    """接收一个版次的一批报文。同版乱序补报按情景和日期取最新修订。"""
    lookup = select(ForecastVersion).where(ForecastVersion.version_no == version_no)
    version = db.scalar(lookup)
    created = version is None
    if created:
        version = ForecastVersion(version_no=version_no, source=source, parts=0, author=actor)
        try:
            with db.begin_nested():
                db.add(version)
                db.flush()
        except IntegrityError:
            version = db.scalar(lookup.with_for_update())
            if version is None:
                raise
            created = False
    version.parts += 1
    stats = {"added": 0, "updated": 0, "kept": 0}
    for row in rows:
        day = date.fromisoformat(row["forecast_date"])
        lookup = select(ScenarioEntry).where(ScenarioEntry.version_id == version.id,
                                             ScenarioEntry.scenario == row["scenario"],
                                             ScenarioEntry.forecast_date == day)
        entry = db.scalar(lookup)
        if entry is None:
            db.add(ScenarioEntry(version_id=version.id, scenario=row["scenario"],
                                 forecast_date=day, net_rise_m=row["net_rise_m"],
                                 weight=row["weight"], revision=row["revision"]))
            stats["added"] += 1
        elif entry.revision < row["revision"]:
            entry.net_rise_m = row["net_rise_m"]
            entry.weight = row["weight"]
            entry.revision = row["revision"]
            stats["updated"] += 1
        else:
            stats["kept"] += 1
    if created:
        invalidate_stale_runs(db, version_no, actor)
    record(db, actor, 'forecast.part_received', version.id,
           version_no=version_no, created=created, **stats)
    return version, created, stats


def invalidate_stale_runs(db, version_no, actor):
    """上游更新版次后，在算或排队的旧版情景计算即刻失效。"""
    query = (select(Run).join(ForecastVersion, Run.version_id == ForecastVersion.id)
             .where(Run.kind == Kind.SCENARIO, Run.status.in_([Status.QUEUED, Status.RUNNING]),
                    ForecastVersion.version_no < version_no))
    for job in db.scalars(query):
        job.status = Status.STALE
        job.lease_until = None
        job.finished_at = now()
        job.error = f'上游已发布第 {version_no} 版情景预报，本次计算失效'
        record(db, actor, 'forecast.run_staled', job.id, version_no=version_no)


def publish(db, job, result):
    """发布情景计算结果：只有更新的版次能接替当前发布，同批重跑不重复发布。"""
    version = db.get(ForecastVersion, job.version_id)
    newer = db.scalar(select(func.count()).select_from(ForecastVersion)
                      .where(ForecastVersion.version_no > version.version_no))
    if newer:
        job.status = Status.STALE
        job.result = None
        job.export_file = None
        job.error = '上游已更新情景预报版次，本次结果失效'
        record(db, 'worker', 'forecast.stale', job.id, version_no=version.version_no)
        return False
    current_no = db.scalar(select(ForecastVersion.version_no)
                           .join(PublishedForecast, PublishedForecast.version_id == ForecastVersion.id)
                           .order_by(ForecastVersion.version_no.desc()).limit(1))
    if current_no is not None and version.version_no <= current_no:
        # 同批重跑或较早版次晚算完，都不能抢回当前发布。
        record(db, 'worker', 'forecast.publish_skipped', job.id,
               version_no=version.version_no, current_version_no=current_no)
        return False
    snapshot = {**result, 'version_no': version.version_no, 'source': version.source}
    try:
        with db.begin_nested():
            db.add(PublishedForecast(version_id=version.id, run_id=job.id, result=snapshot))
            db.flush()
    except IntegrityError:
        record(db, 'worker', 'forecast.publish_skipped', job.id, version_no=version.version_no)
        return False
    record(db, 'worker', 'forecast.published', job.id, version_no=version.version_no)
    return True


def entries_of(db, version_id):
    rows = db.scalars(select(ScenarioEntry).where(ScenarioEntry.version_id == version_id)
                      .order_by(ScenarioEntry.scenario, ScenarioEntry.forecast_date))
    return [{'scenario': row.scenario, 'forecast_date': row.forecast_date.isoformat(),
             'net_rise_m': row.net_rise_m, 'weight': row.weight, 'revision': row.revision}
            for row in rows]
