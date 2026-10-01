from __future__ import annotations

import math
from datetime import date, datetime, timezone

from litestar import Controller, MediaType, Request, get, post
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Redirect, Template
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from charclamp.domain.models import BurnShift, Clamp, User, VolumeCertificate, utcnow
from charclamp.domain.rules import (
    RuleError,
    assert_can_set_clamp_status,
    assert_peak_allowed_by_certificate,
    can_mark_clamp_drawn,
)
from charclamp.infra.db import SessionLocal
from charclamp.infra.security import verify_password

STATUS_LABELS = {
    Clamp.STATUS_STACKED: "已码窑",
    Clamp.STATUS_BURNING: "焖烧中",
    Clamp.STATUS_DRAWN: "已出炭",
}


def _set_flash(request: Request, message: str, category: str = "ok") -> None:
    data = dict(request.session or {})
    data["flash"] = message
    data["flash_cat"] = category
    request.set_session(data)


def _pop_flash(request: Request) -> tuple[str | None, str | None]:
    data = dict(request.session or {})
    message = data.pop("flash", None)
    category = data.pop("flash_cat", None)
    if message is not None or category is not None:
        request.set_session(data)
    return message, category


def _parse_optional_int(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_float(raw: str | None, field_label: str, positive: bool = False) -> float:
    try:
        value = float((raw or "").strip())
    except (TypeError, ValueError):
        raise RuleError(f"{field_label}必须填写数字") from None
    # 拒绝 nan / inf：NaN 的比较恒为 False，会旁路证面上限与出炭门槛。
    if not math.isfinite(value) or (positive and value <= 0):
        if positive:
            raise RuleError(f"{field_label}必须为正数")
        raise RuleError(f"{field_label}必须为有限数字")
    return value


def _parse_positive_float(raw: str | None, field_label: str) -> float:
    return _parse_float(raw, field_label, positive=True)


async def _load_clamps_with_certs(db) -> list[Clamp]:
    return list(
        (
            await db.execute(
                select(Clamp)
                .options(
                    selectinload(Clamp.site),
                    selectinload(Clamp.shifts),
                    selectinload(Clamp.certificates),
                )
                .order_by(Clamp.code)
            )
        )
        .scalars()
        .all()
    )


async def _load_timeline_context(clamp_id: int | None = None) -> dict:
    async with SessionLocal() as db:
        clamps = await _load_clamps_with_certs(db)
        query = (
            select(BurnShift)
            .options(selectinload(BurnShift.clamp).selectinload(Clamp.site))
            .order_by(BurnShift.started_at.desc())
        )
        if clamp_id is not None:
            query = query.where(BurnShift.clamp_id == clamp_id)
        shifts = list((await db.execute(query)).scalars().all())
        site_name = clamps[0].site.name if clamps else "乌石岗焖烧坞"
        from charclamp.domain.rules import active_certificate

        cert_by_clamp = {clamp.id: active_certificate(clamp) for clamp in clamps}
    return {
        "clamps": clamps,
        "shifts": shifts,
        "active_clamp_id": clamp_id,
        "status_labels": STATUS_LABELS,
        "site_name": site_name,
        "cert_by_clamp": cert_by_clamp,
    }


class AuthController(Controller):
    path = ""
    tags = ["auth"]

    @get("/login", media_type=MediaType.HTML)
    async def login_page(self, request: Request) -> Template:
        flash, flash_cat = _pop_flash(request)
        return Template(
            template_name="login.html",
            context={"flash": flash, "flash_cat": flash_cat},
        )

    @post("/login")
    async def login(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        async with SessionLocal() as db:
            result = await db.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if not user or not verify_password(password, user.password_hash):
                request.set_session({"flash": "用户名或密码错误", "flash_cat": "error"})
                return Redirect("/login")
            request.set_session({"user_id": user.id})
        return Redirect("/")

    @get("/logout")
    async def logout(self, request: Request) -> Redirect:
        request.clear_session()
        return Redirect("/login")


class TimelineController(Controller):
    path = ""
    tags = ["timeline"]

    @get("/", media_type=MediaType.HTML)
    async def timeline(self, request: Request) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        flash, flash_cat = _pop_flash(request)
        clamp_id = _parse_optional_int(request.query_params.get("clamp_id"))
        ctx = await _load_timeline_context(clamp_id)
        return Template(
            template_name="timeline.html",
            context={
                **ctx,
                "user": request.user,
                "flash": flash,
                "flash_cat": flash_cat,
            },
        )

    @get("/timeline/partial", media_type=MediaType.HTML)
    async def timeline_partial(self, request: Request) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        clamp_id = _parse_optional_int(request.query_params.get("clamp_id"))
        ctx = await _load_timeline_context(clamp_id)
        return Template(
            template_name="partials/board.html",
            context={
                **ctx,
                "user": request.user,
            },
        )

    @get("/drawer/shift-new", media_type=MediaType.HTML)
    async def drawer_shift_new(self, request: Request) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        clamp_id = _parse_optional_int(request.query_params.get("clamp_id"))
        async with SessionLocal() as db:
            clamps = await _load_clamps_with_certs(db)
        from charclamp.domain.rules import active_certificate

        return Template(
            template_name="partials/drawer_shift.html",
            context={
                "clamps": clamps,
                "preselect_clamp_id": clamp_id,
                "user": request.user,
                "cert_by_clamp": {clamp.id: active_certificate(clamp) for clamp in clamps},
            },
        )

    @get("/drawer/clamp/{clamp_id:int}", media_type=MediaType.HTML)
    async def drawer_clamp(self, request: Request, clamp_id: int) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        async with SessionLocal() as db:
            result = await db.execute(
                select(Clamp)
                .where(Clamp.id == clamp_id)
                .options(
                    selectinload(Clamp.shifts),
                    selectinload(Clamp.site),
                    selectinload(Clamp.certificates),
                )
            )
            clamp = result.scalar_one_or_none()
            if not clamp:
                return Redirect("/")
            latest_shift = (
                max(clamp.shifts, key=lambda s: s.started_at) if clamp.shifts else None
            )
        from charclamp.domain.rules import active_certificate

        cert = active_certificate(clamp)
        can_drawn, drawn_msg = can_mark_clamp_drawn(clamp)
        return Template(
            template_name="partials/drawer_clamp.html",
            context={
                "clamp": clamp,
                "latest_shift": latest_shift,
                "active_cert": cert,
                "status_labels": STATUS_LABELS,
                "can_drawn": can_drawn,
                "drawn_msg": drawn_msg,
                "user": request.user,
            },
        )


class ShiftController(Controller):
    path = "/shifts"
    tags = ["shifts"]

    @post("/new")
    async def create_shift(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        if not request.user:
            return Redirect("/login")
        clamp_id = int(data["clamp_id"])
        started_raw = data.get("started_at") or ""
        if started_raw:
            started_at = datetime.fromisoformat(started_raw)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
        else:
            started_at = utcnow()
        peak_raw = (data.get("peak_temp_c") or "").strip()
        try:
            peak = _parse_float(peak_raw, "峰值温度") if peak_raw else None
        except RuleError as exc:
            _set_flash(request, str(exc), "error")
            return Redirect(f"/?clamp_id={clamp_id}")
        async with SessionLocal() as db:
            clamp = (
                await db.execute(
                    select(Clamp)
                    .where(Clamp.id == clamp_id)
                    .options(selectinload(Clamp.certificates))
                )
            ).scalar_one_or_none()
            if not clamp:
                return Redirect("/")
            try:
                # 抽屉提交：无证或峰值超证面上限一律拒绝。
                assert_peak_allowed_by_certificate(clamp, peak)
            except RuleError as exc:
                _set_flash(request, str(exc), "error")
                return Redirect(f"/?clamp_id={clamp_id}")
            shift = BurnShift(
                clamp_id=clamp_id,
                started_at=started_at,
                peak_temp_c=peak,
                charcoal_grade=(data.get("charcoal_grade") or "B").strip(),
                notes=(data.get("notes") or "").strip(),
            )
            db.add(shift)
            if clamp.status == Clamp.STATUS_STACKED:
                clamp.status = Clamp.STATUS_BURNING
            await db.commit()
        _set_flash(request, "焖烧班次已登记", "ok")
        return Redirect(f"/?clamp_id={clamp_id}")

    @post("/{shift_id:int}/peak")
    async def save_peak(
        self,
        request: Request,
        shift_id: int,
        data: dict = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        """改写（补录）已登记班次的峰值温度，走与抽屉提交同一句校验。"""
        if not request.user:
            return Redirect("/login")
        peak_raw = (data.get("peak_temp_c") or "").strip()
        try:
            peak = _parse_float(peak_raw, "峰值温度") if peak_raw else None
        except RuleError as exc:
            _set_flash(request, str(exc), "error")
            return Redirect("/")
        async with SessionLocal() as db:
            shift = (
                await db.execute(
                    select(BurnShift)
                    .where(BurnShift.id == shift_id)
                    .options(selectinload(BurnShift.clamp).selectinload(Clamp.certificates))
                )
            ).scalar_one_or_none()
            if not shift:
                return Redirect("/")
            try:
                # 保存接口：与抽屉提交共用同一句中文提示。
                assert_peak_allowed_by_certificate(shift.clamp, peak)
            except RuleError as exc:
                _set_flash(request, str(exc), "error")
                return Redirect(f"/?clamp_id={shift.clamp_id}")
            shift.peak_temp_c = peak
            await db.commit()
        _set_flash(request, f"班次峰值已保存为 {peak:g}℃", "ok")
        return Redirect(f"/?clamp_id={shift.clamp_id}")


class CertificateController(Controller):
    path = "/certificates"
    tags = ["certificates"]

    @get(["", "/"], media_type=MediaType.HTML, include_in_schema=False)
    async def list_certificates(self, request: Request) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        flash, flash_cat = _pop_flash(request)
        async with SessionLocal() as db:
            clamps = await _load_clamps_with_certs(db)
            certs = list(
                (
                    await db.execute(
                        select(VolumeCertificate)
                        .options(
                            selectinload(VolumeCertificate.clamp).selectinload(Clamp.site)
                        )
                        .order_by(
                            VolumeCertificate.revoked_at.is_not(None),
                            VolumeCertificate.effective_date.desc(),
                            VolumeCertificate.id.desc(),
                        )
                    )
                )
                .scalars()
                .all()
            )
            active_count = sum(1 for c in certs if c.revoked_at is None)
        from charclamp.domain.rules import active_certificate

        return Template(
            template_name="certificates.html",
            context={
                "certs": certs,
                "clamps": clamps,
                "active_count": active_count,
                "cert_by_clamp": {clamp.id: active_certificate(clamp) for clamp in clamps},
                "user": request.user,
                "today": utcnow().date().isoformat(),
                "flash": flash,
                "flash_cat": flash_cat,
            },
        )

    @post("/new")
    async def issue_certificate(
        self,
        request: Request,
        data: dict = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        if not request.user:
            return Redirect("/login")
        try:
            clamp_id = int(data.get("clamp_id"))
        except (TypeError, ValueError):
            _set_flash(request, "请选择炭窑", "error")
            return Redirect("/certificates")
        try:
            volume = _parse_positive_float(data.get("volume_m3"), "窑膛容积")
            temp_limit = _parse_positive_float(data.get("temp_limit_c"), "温度上限")
        except RuleError as exc:
            _set_flash(request, str(exc), "error")
            return Redirect("/certificates")
        effective_raw = (data.get("effective_date") or "").strip()
        try:
            effective_date = (
                datetime.strptime(effective_raw, "%Y-%m-%d").date()
                if effective_raw
                else utcnow().date()
            )
        except ValueError:
            _set_flash(request, "生效日格式无效", "error")
            return Redirect("/certificates")
        issued_by = (data.get("issued_by") or "").strip() or request.user.username

        async with SessionLocal() as db:
            clamp = (
                await db.execute(
                    select(Clamp)
                    .where(Clamp.id == clamp_id)
                    .options(selectinload(Clamp.certificates))
                )
            ).scalar_one_or_none()
            if not clamp:
                _set_flash(request, "炭窑不存在", "error")
                return Redirect("/certificates")
            from charclamp.domain.rules import active_certificate

            if active_certificate(clamp) is not None:
                _set_flash(
                    request,
                    "该窑已有未作废容积证，不能重复办证；如需换证请先由管理员作废旧证",
                    "error",
                )
                return Redirect("/certificates")
            cert = VolumeCertificate(
                clamp_id=clamp_id,
                volume_m3=volume,
                temp_limit_c=temp_limit,
                effective_date=effective_date,
                issued_by=issued_by,
            )
            db.add(cert)
            try:
                await db.commit()
            except IntegrityError:
                # 两名操作工并发抢证：部分唯一索引只放行一张。
                await db.rollback()
                _set_flash(
                    request,
                    "该窑已有未作废容积证，不能重复办证；如需换证请先由管理员作废旧证",
                    "error",
                )
                return Redirect("/certificates")
        _set_flash(request, f"窑 {clamp.code} 的窑膛容积证已签发", "ok")
        return Redirect("/certificates")

    @post("/{cert_id:int}/revoke")
    async def revoke_certificate(
        self,
        request: Request,
        cert_id: int,
        data: dict = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        if not request.user:
            return Redirect("/login")
        if request.user.role != "admin":
            _set_flash(request, "只有管理员才能作废容积证", "error")
            return Redirect("/certificates")
        async with SessionLocal() as db:
            cert = (
                await db.execute(
                    select(VolumeCertificate)
                    .where(VolumeCertificate.id == cert_id)
                    .options(selectinload(VolumeCertificate.clamp))
                )
            ).scalar_one_or_none()
            if not cert:
                _set_flash(request, "容积证不存在", "error")
                return Redirect("/certificates")
            if cert.revoked_at is not None:
                _set_flash(request, "该容积证已作废，无需重复操作", "error")
                return Redirect("/certificates")
            cert.revoked_at = utcnow()
            await db.commit()
        _set_flash(request, f"窑 {cert.clamp.code} 的容积证已作废，不再约束班次峰值", "ok")
        return Redirect("/certificates")


class ClampController(Controller):
    path = "/clamps"
    tags = ["clamps"]

    @post("/{clamp_id:int}/status")
    async def set_status(
        self,
        request: Request,
        clamp_id: int,
        data: dict = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        if not request.user:
            return Redirect("/login")
        new_status = (data.get("status") or "").strip()
        async with SessionLocal() as db:
            result = await db.execute(
                select(Clamp)
                .where(Clamp.id == clamp_id)
                .options(selectinload(Clamp.shifts))
            )
            clamp = result.scalar_one_or_none()
            if not clamp:
                return Redirect("/")
            try:
                assert_can_set_clamp_status(clamp, new_status)
                clamp.status = new_status
                await db.commit()
                _set_flash(request, f"窑 {clamp.code} 状态已更新", "ok")
            except RuleError as exc:
                _set_flash(request, str(exc), "error")
        return Redirect(f"/?clamp_id={clamp_id}")
