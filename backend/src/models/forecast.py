from datetime import date, datetime
from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.utils.time import now

class ForecastVersion(Base):
    __tablename__ = 'forecast_versions'

    id: Mapped[int] = mapped_column(primary_key=True)
    version_no: Mapped[int] = mapped_column(unique=True, index=True)
    source: Mapped[str] = mapped_column(String(120))
    parts: Mapped[int] = mapped_column(default=0)
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)

class ScenarioEntry(Base):
    __tablename__ = 'scenario_entries'
    __table_args__ = (UniqueConstraint('version_id', 'scenario', 'forecast_date'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey('forecast_versions.id'), index=True)
    scenario: Mapped[str] = mapped_column(String(40))
    forecast_date: Mapped[date]
    net_rise_m: Mapped[float]
    weight: Mapped[float]
    revision: Mapped[int]

class PublishedForecast(Base):
    __tablename__ = 'published_forecasts'

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey('forecast_versions.id'), unique=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('runs.id'))
    result: Mapped[dict] = mapped_column(JSON)
    published_at: Mapped[datetime] = mapped_column(default=now)
