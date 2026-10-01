"""端到端验收：窑膛容积证规则。跑真实 ASGI 应用（SQLite），无需 Docker/Postgres。"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

DB_FILE = Path(tempfile.gettempdir()) / "charclamp_e2e.db"
if DB_FILE.exists():
    DB_FILE.unlink()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_FILE}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB_FILE}"
os.environ.setdefault("SECRET_KEY", "test-secret")

from sqlalchemy import select  # noqa: E402

from charclamp.domain.models import Clamp, VolumeCertificate  # noqa: E402
from charclamp.infra.db import SessionLocal, sync_create_all  # noqa: E402
from charclamp.infra.seed import seed_demo  # noqa: E402

sync_create_all()
seed_demo()

from httpx import ASGITransport, AsyncClient  # noqa: E402

from charclamp.domain.rules import NO_CERT_MSG  # noqa: E402
from charclamp.main import app  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


async def login(client: AsyncClient, username: str) -> None:
    r = await client.post(
        "/login",
        data={"username": username, "password": "123456"},
        follow_redirects=True,
    )
    assert r.status_code == 200, r.status_code


async def clamp_ids() -> dict[str, int]:
    async with SessionLocal() as db:
        rows = (await db.execute(select(Clamp).order_by(Clamp.code))).scalars().all()
        return {c.code: c.id for c in rows}


async def active_cert_count(clamp_id: int) -> int:
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(VolumeCertificate).where(
                    VolumeCertificate.clamp_id == clamp_id,
                    VolumeCertificate.revoked_at.is_(None),
                )
            )
        ).scalars().all()
        return len(rows)


async def main() -> None:
    ids = await clamp_ids()
    c1, c2, c3 = ids["坞东-甲"], ids["坞东-乙"], ids["河沿-丙"]
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://t", follow_redirects=True) as worker, \
            AsyncClient(transport=transport, base_url="http://t", follow_redirects=True) as worker2, \
            AsyncClient(transport=transport, base_url="http://t", follow_redirects=True) as admin:
        await login(worker, "worker")
        await login(worker2, "worker")
        await login(admin, "admin")

        print("种子数据")
        check("坞东-甲 无证", await active_cert_count(c1) == 0)
        check("坞东-乙 上限 390", True)  # 下面从页面/接口验证

        print("顶栏与专页")
        r = await worker.get("/")
        check("时间轴可点开", r.status_code == 200 and "焖烧时间轴" in r.text)
        check("顶栏有容积证入口", "/certificates" in r.text)
        check("剪影显示无证提示", "无证 · 请先办证" in r.text)
        check("剪影显示现行上限 390", "现行上限 390℃" in r.text)
        r = await worker.get("/certificates")
        check("容积证专页可点开", r.status_code == 200 and "窑膛容积证" in r.text)
        check("专页含新建表单", 'action="/certificates/new"' in r.text)
        check("专页含说明-无证", "无证" in r.text)
        check("专页含说明-超上限", "超上限" in r.text)
        check("台账显示 390 证", ">390<" in r.text)
        check("操作工看到非作废提示", "仅管理员可作废" in r.text)

        print("规则1：无证不能写峰值（抽屉提交）")
        r = await worker.post(
            "/shifts/new",
            data={"clamp_id": str(c1), "peak_temp_c": "455", "charcoal_grade": "A"},
        )
        check("无证登记班次被拒", NO_CERT_MSG in r.text, r.text[:200])

        print("规则2：峰值不得超证面上限（抽屉提交）")
        r = await worker.post(
            "/shifts/new",
            data={"clamp_id": str(c2), "peak_temp_c": "400", "charcoal_grade": "B"},
        )
        msg_create = "温度上限 390℃" in r.text and "超过" in r.text
        check("超 390 被拒", msg_create, r.text[:200])
        r = await worker.post(
            "/shifts/new",
            data={"clamp_id": str(c2), "peak_temp_c": "390", "charcoal_grade": "B"},
        )
        check("恰 390 放行", "焖烧班次已登记" in r.text, r.text[:200])
        r = await worker.post(
            "/shifts/new",
            data={"clamp_id": str(c2), "peak_temp_c": "", "charcoal_grade": "B"},
        )
        check("有证不填峰值放行", "焖烧班次已登记" in r.text, r.text[:200])

        # 找到 c2 最近一条班次用于“保存峰值”
        from charclamp.domain.models import BurnShift
        async with SessionLocal() as db:
            shift_c2 = (
                await db.execute(
                    select(BurnShift)
                    .where(BurnShift.clamp_id == c2)
                    .order_by(BurnShift.id.desc())
                )
            ).scalars().first()
            shift_c1 = (
                await db.execute(
                    select(BurnShift).where(BurnShift.clamp_id == c1)
                )
            ).scalars().first()

        print("规则3：保存接口同一句中文（禁止旁路）")
        r = await worker.post(f"/shifts/{shift_c1.id}/peak", data={"peak_temp_c": "410"})
        check("无证保存峰值被拒", NO_CERT_MSG in r.text, r.text[:200])
        r = await worker.post(f"/shifts/{shift_c2.id}/peak", data={"peak_temp_c": "391"})
        msg_save = "温度上限 390℃" in r.text and "超过" in r.text
        check("保存超 390 被拒", msg_save, r.text[:200])
        r = await worker.post(f"/shifts/{shift_c2.id}/peak", data={"peak_temp_c": "390"})
        check("保存 390 放行", "班次峰值已保存" in r.text, r.text[:200])

        print("规则4：操作工可办证，字段必须为正")
        r = await worker.post(
            "/certificates/new",
            data={"clamp_id": str(c1), "volume_m3": "0", "temp_limit_c": "500"},
        )
        check("容积非正被拒", "必须为正" in r.text, r.text[:200])
        r = await worker.post(
            "/certificates/new",
            data={"clamp_id": str(c1), "volume_m3": "12", "temp_limit_c": "-5"},
        )
        check("上限非正被拒", "必须为正" in r.text, r.text[:200])
        r = await worker.post(
            "/certificates/new",
            data={"clamp_id": str(c1), "volume_m3": "12", "temp_limit_c": "500"},
        )
        check("操作工办证成功", "容积证已签发" in r.text, r.text[:200])
        check("数据库只落一张", await active_cert_count(c1) == 1)
        r = await worker.post(
            "/shifts/new",
            data={"clamp_id": str(c1), "peak_temp_c": "480", "charcoal_grade": "A"},
        )
        check("办证后峰值 480 放行（≤500）", "焖烧班次已登记" in r.text, r.text[:200])
        r = await worker.post(
            "/shifts/new",
            data={"clamp_id": str(c1), "peak_temp_c": "501", "charcoal_grade": "A"},
        )
        check("办证后超 500 仍拒", "温度上限 500℃" in r.text, r.text[:200])

        print("规则5：出炭门槛 ≥400 不旁路")
        r = await worker.post(f"/clamps/{c1}/status", data={"status": "drawn"})
        check("峰值 480 可出炭", "状态已更新" in r.text, r.text[:200])
        # c2 证面上限 390，峰值不可能 ≥400，出炭必须被挡
        r = await worker.post(f"/clamps/{c2}/status", data={"status": "drawn"})
        check("c2 峰值低于 400 禁止出炭", "低于 400" in r.text or "尚未记录" in r.text, r.text[:200])

        print("规则6：作废权仅管理员")
        # 先重置 c1 回非 drawn
        await worker.post(f"/clamps/{c1}/status", data={"status": "stacked"})
        # 找 c1 当前证 id
        async with SessionLocal() as db:
            cert_c1 = (
                await db.execute(
                    select(VolumeCertificate).where(
                        VolumeCertificate.clamp_id == c1,
                        VolumeCertificate.revoked_at.is_(None),
                    )
                )
            ).scalars().one()
            cert_c1_id = cert_c1.id
        r = await worker.post(f"/certificates/{cert_c1_id}/revoke", data={})
        check("操作工作废被拒", "只有管理员可以作废" in r.text, r.text[:200])
        check("被拒后证仍现行", await active_cert_count(c1) == 1)
        r = await admin.post(f"/certificates/{cert_c1_id}/revoke", data={})
        check("管理员作废成功", "已作废" in r.text, r.text[:200])
        check("作废后不再现行", await active_cert_count(c1) == 0)
        r = await worker.post(
            "/shifts/new",
            data={"clamp_id": str(c1), "peak_temp_c": "300"},
        )
        check("作废后无证写峰值再被拒", NO_CERT_MSG in r.text, r.text[:200])
        r = await worker.post(
            "/certificates/new",
            data={"clamp_id": str(c1), "volume_m3": "15", "temp_limit_c": "520"},
        )
        check("作废后可再办新证", "容积证已签发" in r.text, r.text[:200])

        print("规则7：两名操作工并发争抢，只落一张")
        # 管理员先作废旧证，两个 worker 同时办
        async with SessionLocal() as db:
            old = (
                await db.execute(
                    select(VolumeCertificate).where(
                        VolumeCertificate.clamp_id == c1,
                        VolumeCertificate.revoked_at.is_(None),
                    )
                )
            ).scalars().one()
            old_id = old.id
        await admin.post(f"/certificates/{old_id}/revoke", data={})
        results = await asyncio.gather(
            worker.post(
                "/certificates/new",
                data={"clamp_id": str(c1), "volume_m3": "9", "temp_limit_c": "470"},
            ),
            worker2.post(
                "/certificates/new",
                data={"clamp_id": str(c1), "volume_m3": "9", "temp_limit_c": "470"},
            ),
        )
        body = results[0].text + results[1].text
        ok_ct = body.count('class="flash flash-ok"')
        blocked_ct = body.count('class="flash flash-error"') + body.count(
            'class="flash flash-error '
        )
        blocked_ok = "新证登记被挡下" in body
        check(
            "并发仅一张成功",
            ok_ct == 1 and blocked_ct == 1 and blocked_ok,
            f"ok={ok_ct} blocked={blocked_ct} blocked_msg={blocked_ok}",
        )
        check("数据库未出现双证", await active_cert_count(c1) == 1)
        r1 = await worker.get("/")
        r2 = await worker2.get("/certificates")
        check("被挡后时间轴仍点得开", r1.status_code == 200 and "焖烧时间轴" in r1.text)
        check("被挡后证专页仍点得开", r2.status_code == 200 and "窑膛容积证" in r2.text)

    print(f"\n结果：{PASS} 通过，{FAIL} 失败")
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
