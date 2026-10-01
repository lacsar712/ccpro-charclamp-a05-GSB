from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import text as sql_text


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="worker")


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    clamps: Mapped[list[Clamp]] = relationship(back_populates="site", cascade="all, delete-orphan")


class Clamp(Base):
    __tablename__ = "clamps"
    __table_args__ = (UniqueConstraint("site_id", "code", name="uq_clamp_code_per_site"),)

    STATUS_STACKED = "stacked"
    STATUS_BURNING = "burning"
    STATUS_DRAWN = "drawn"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_STACKED)
    wood_species: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    site: Mapped[Site] = relationship(back_populates="clamps")
    shifts: Mapped[list[BurnShift]] = relationship(
        back_populates="clamp",
        cascade="all, delete-orphan",
    )
    certificates: Mapped[list[VolumeCertificate]] = relationship(
        back_populates="clamp",
        cascade="all, delete-orphan",
    )

    def active_certificate(self) -> "VolumeCertificate | None":
        """该窑现行（未作废）窑膛容积证；至多一张。"""
        active = [c for c in self.certificates if c.revoked_at is None]
        if not active:
            return None
        return max(active, key=lambda c: c.effective_date)


class BurnShift(Base):
    __tablename__ = "burn_shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clamp_id: Mapped[int] = mapped_column(ForeignKey("clamps.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    peak_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    charcoal_grade: Mapped[str] = mapped_column(String(40), nullable=False, default="B")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    clamp: Mapped[Clamp] = relationship(back_populates="shifts")


class VolumeCertificate(Base):
    """窑膛容积证：班次峰值不得超过证面温度上限。"""

    __tablename__ = "volume_certificates"
    __table_args__ = (
        # 同一炭窑未作废证最多一张——数据库层兜底，并发争抢也只落一张。
        Index(
            "uq_volume_cert_active_per_clamp",
            "clamp_id",
            unique=True,
            postgresql_where=sql_text("revoked_at IS NULL"),
            sqlite_where=sql_text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clamp_id: Mapped[int] = mapped_column(ForeignKey("clamps.id"), nullable=False)
    volume_m3: Mapped[float] = mapped_column(Float, nullable=False)
    temp_limit_c: Mapped[float] = mapped_column(Float, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, default=lambda: utcnow().date())
    issued_by: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    clamp: Mapped[Clamp] = relationship(back_populates="certificates")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
