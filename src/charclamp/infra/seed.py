from __future__ import annotations

from datetime import timedelta

from charclamp.domain.models import BurnShift, Clamp, Site, User, VolumeCertificate, utcnow
from charclamp.infra.db import SyncSessionLocal
from charclamp.infra.security import hash_password


def _backfill_certificates(session) -> None:
    """旧库升级：证件表为空时补齐演示容积证（仅一次，幂等）。"""
    if session.query(VolumeCertificate).first():
        return
    by_code = {c.code: c for c in session.query(Clamp).all()}
    today = utcnow().date()
    certs = []
    if (c2 := by_code.get("坞东-乙")) is not None:
        certs.append(
            VolumeCertificate(clamp=c2, volume_m3=6.0, temp_limit_c=390.0,
                              effective_date=today, issued_by="admin")
        )
    if (c3 := by_code.get("河沿-丙")) is not None:
        certs.append(
            VolumeCertificate(clamp=c3, volume_m3=20.0, temp_limit_c=600.0,
                              effective_date=today, issued_by="admin")
        )
    # 坞东-甲 故意保持无证。
    session.add_all(certs)


def seed_demo() -> None:
    with SyncSessionLocal() as session:
        admin = session.query(User).filter_by(username="admin").first()
        if not admin:
            admin = User(username="admin", role="admin", password_hash=hash_password("123456"))
            session.add(admin)
        else:
            admin.password_hash = hash_password("123456")
            admin.role = "admin"

        worker = session.query(User).filter_by(username="worker").first()
        if not worker:
            worker = User(username="worker", role="worker", password_hash=hash_password("123456"))
            session.add(worker)
        else:
            worker.password_hash = hash_password("123456")
            worker.role = "worker"

        if session.query(Site).first():
            _backfill_certificates(session)
            session.commit()
            return

        site = Site(name="乌石岗焖烧坞", location="河谷台地北侧", notes="青冈为主，夜班闷窑")
        session.add(site)
        session.flush()

        c1 = Clamp(site=site, code="坞东-甲", status=Clamp.STATUS_BURNING, wood_species="青冈")
        c2 = Clamp(site=site, code="坞东-乙", status=Clamp.STATUS_STACKED, wood_species="松木")
        c3 = Clamp(site=site, code="河沿-丙", status=Clamp.STATUS_DRAWN, wood_species="栎木")
        session.add_all([c1, c2, c3])
        session.flush()

        now = utcnow()
        session.add_all(
            [
                BurnShift(
                    clamp=c1,
                    started_at=now - timedelta(hours=10),
                    peak_temp_c=455.0,
                    charcoal_grade="A",
                    notes="峰值已过，可出炭（办证前的旧班次）",
                ),
                BurnShift(
                    clamp=c2,
                    started_at=now - timedelta(hours=3),
                    peak_temp_c=None,
                    charcoal_grade="B",
                    notes="刚点火，未测峰值",
                ),
                BurnShift(
                    clamp=c3,
                    started_at=now - timedelta(days=2),
                    peak_temp_c=520.0,
                    charcoal_grade="A+",
                    notes="已出炭班次",
                ),
            ]
        )
        session.flush()

        # 窑膛容积证种子：
        # 坞东-甲（c1）故意无证——演示“先办证”；
        # 坞东-乙（c2）温度上限写成 390℃——演示超上限；
        # 河沿-丙（c3）持现行有效证。
        today = now.date()
        session.add_all(
            [
                VolumeCertificate(
                    clamp=c2,
                    volume_m3=6.0,
                    temp_limit_c=390.0,
                    effective_date=today,
                    issued_by="admin",
                ),
                VolumeCertificate(
                    clamp=c3,
                    volume_m3=20.0,
                    temp_limit_c=600.0,
                    effective_date=today,
                    issued_by="admin",
                ),
            ]
        )
        session.commit()
