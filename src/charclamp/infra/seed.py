from __future__ import annotations

from datetime import timedelta

from charclamp.domain.models import (
    BurnShift,
    Clamp,
    Site,
    User,
    VolumeCertificate,
    utcnow,
)
from charclamp.infra.db import SyncSessionLocal
from charclamp.infra.security import hash_password


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

        today = utcnow().date()
        session.add_all(
            [
                # 坞东-甲：现行证温度上限写成 390℃（演示超上限拦截：为该窑新登记 391℃ 及以上峰值即被拒）
                VolumeCertificate(
                    clamp=c1,
                    volume_m3=18.5,
                    temp_limit_c=390.0,
                    effective_date=today - timedelta(days=30),
                    issued_by="admin",
                ),
                # 坞东-乙：不办容积证（演示无证拦截：登记峰值前须先办证）
                # 河沿-丙：上限 550℃ 的正常现行证
                VolumeCertificate(
                    clamp=c3,
                    volume_m3=24.0,
                    temp_limit_c=550.0,
                    effective_date=today - timedelta(days=90),
                    issued_by="admin",
                ),
            ]
        )

        now = utcnow()
        session.add_all(
            [
                BurnShift(
                    clamp=c1,
                    started_at=now - timedelta(hours=10),
                    peak_temp_c=385.0,
                    charcoal_grade="A",
                    notes="峰值 385℃ 未过出炭门槛；该窑现行证上限 390℃，补录 400℃ 会被超上限拦截",
                ),
                BurnShift(
                    clamp=c2,
                    started_at=now - timedelta(hours=3),
                    peak_temp_c=None,
                    charcoal_grade="B",
                    notes="刚点火，未测峰值；该窑暂无容积证",
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
        session.commit()
