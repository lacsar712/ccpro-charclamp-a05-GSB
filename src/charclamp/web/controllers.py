from __future__ import annotations

from datetime import date, datetime
from typing import Any

from litestar import Controller, MediaType, Request, get, post
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Redirect, Template
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from charclamp.domain.models import BurnShift, Clamp, User, VolumeCertificate, utcnow
from charclamp.domain.rules import (
    NO_CERT_MSG,
    RuleError,
    assert_can_set_clamp_status,
    assert_peak_allowed,
    assert_valid_certificate_fields,
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


def _parse_optional_float(raw: str | None) -> float | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _parse_positive_date(raw: str | None) -> date:
    if raw:
        try:
            return date.fromisoformat(raw.strip())
        except ValueError:
            pass
    return utcnow().date()


def _is_admin(request: Request) -> bool:
    return bool(request.user and getattr(request.user, "role", None) == "admin")


async def _load_clamp_with_rules(db, clamp_id: int) -> Clamp | None:
    result = await db.execute(
        select(Clamp)
        .where(Clamp.id == clamp_id)
        .options(
            selectinload(Clamp.shifts),
            selectinload(Clamp.certificates),
            selectinload(Clamp.site),
        )
    )
    return result.scalar_one_or_none()


async def _load_timeline_context(clamp_id: int | None = None) -> dict[str, Any]:
    async with SessionLocal() as db:
        clamps = list(
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
        query = (
            select(BurnShift)
            .options(selectinload(BurnShift.clamp).selectinload(Clamp.site))
            .order_by(BurnShift.started_at.desc())
        )
        if clamp_id is not None:
            query = query.where(BurnShift.clamp_id == clamp_id)
        shifts = list((await db.execute(query)).scalars().all())
        site_name = clamps[0].site.name if clamps else "乌石岗焖烧坞"
        active_limits = {c.id: (c.active_certificate().temp_limit_c if c.active_certificate() else None) for c in clamps}
    return {
        "clamps": clamps,
        "shifts": shifts,
        "active_clamp_id": clamp_id,
        "status_labels": STATUS_LABELS,
        "site_name": site_name,
        "active_limits": active_limits,
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
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
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

    @get("/certificates", media_type=MediaType.HTML)
    async def certificates_page(self, request: Request) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        flash, flash_cat = _pop_flash(request)
        async with SessionLocal() as db:
            certs = list(
                (
                    await db.execute(
                        select(VolumeCertificate)
                        .options(
                            selectinload(VolumeCertificate.clamp).selectinload(Clamp.site),
                        )
                        .order_by(
                            VolumeCertificate.revoked_at.is_(None).desc(),
                            VolumeCertificate.effective_date.desc(),
                            VolumeCertificate.id.desc(),
                        )
                    )
                )
                .scalars()
                .all()
            )
            clamps = list(
                (
                    await db.execute(
                        select(Clamp)
                        .options(selectinload(Clamp.certificates))
                        .order_by(Clamp.code)
                    )
                )
                .scalars()
                .all()
            )
        cert_by_clamp = {c.id: c.active_certificate() for c in clamps}
        return Template(
            template_name="certificates.html",
            context={
                "certs": certs,
                "clamps": clamps,
                "cert_by_clamp": cert_by_clamp,
                "is_admin": _is_admin(request),
                "flash": flash,
                "flash_cat": flash_cat,
                "user": request.user,
                "site_name": "乌石岗焖烧坞",
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
            clamps = list(
                (
                    await db.execute(
                        select(Clamp)
                        .options(selectinload(Clamp.certificates))
                        .order_by(Clamp.code)
                    )
                )
                .scalars()
                .all()
            )
            cert_by_clamp = {c.id: c.active_certificate() for c in clamps}
        return Template(
            template_name="partials/drawer_shift.html",
            context={
                "clamps": clamps,
                "cert_by_clamp": cert_by_clamp,
                "preselect_clamp_id": clamp_id,
                "no_cert_msg": NO_CERT_MSG,
                "user": request.user,
            },
        )

    @get("/drawer/peak/{shift_id:int}", media_type=MediaType.HTML)
    async def drawer_peak_edit(self, request: Request, shift_id: int) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        async with SessionLocal() as db:
            result = await db.execute(
                select(BurnShift)
                .where(BurnShift.id == shift_id)
                .options(
                    selectinload(BurnShift.clamp).selectinload(Clamp.certificates),
                )
            )
            shift = result.scalar_one_or_none()
            if not shift:
                return Redirect("/")
            cert = shift.clamp.active_certificate()
        return Template(
            template_name="partials/drawer_peak.html",
            context={
                "shift": shift,
                "cert": cert,
                "no_cert_msg": NO_CERT_MSG,
                "user": request.user,
            },
        )

    @get("/drawer/clamp/{clamp_id:int}", media_type=MediaType.HTML)
    async def drawer_clamp(self, request: Request, clamp_id: int) -> Template | Redirect:
        if not request.user:
            return Redirect("/login")
        async with SessionLocal() as db:
            clamp = await _load_clamp_with_rules(db, clamp_id)
            if not clamp:
                return Redirect("/")
            active_cert = clamp.active_certificate()
        can_drawn, drawn_msg = can_mark_clamp_drawn(clamp)
        return Template(
            template_name="partials/drawer_clamp.html",
            context={
                "clamp": clamp,
                "active_cert": active_cert,
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
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        if not request.user:
            return Redirect("/login")
        started_raw = data.get("started_at") or ""
        started_at = datetime.fromisoformat(started_raw) if started_raw else utcnow()
        peak = _parse_optional_float(data.get("peak_temp_c"))
        clamp_id = int(data["clamp_id"])
        async with SessionLocal() as db:
            clamp = await _load_clamp_with_rules(db, clamp_id)
            if not clamp:
                return Redirect("/")
            try:
                # 抽屉提交：无证拒绝，峰值填写不得超证面上限（禁止旁路）。
                assert_peak_allowed(clamp, peak)
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
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        """保存（改写）班次峰值——与抽屉提交共用同一条容积证规则。"""
        if not request.user:
            return Redirect("/login")
        peak = _parse_optional_float(data.get("peak_temp_c"))
        async with SessionLocal() as db:
            result = await db.execute(
                select(BurnShift)
                .where(BurnShift.id == shift_id)
                .options(selectinload(BurnShift.clamp).selectinload(Clamp.certificates))
            )
            shift = result.scalar_one_or_none()
            if not shift:
                return Redirect("/")
            clamp = shift.clamp
            try:
                assert_peak_allowed(clamp, peak)
            except RuleError as exc:
                _set_flash(request, str(exc), "error")
                return Redirect(f"/?clamp_id={clamp.id}")
            shift.peak_temp_c = peak
            await db.commit()
        _set_flash(request, "班次峰值已保存", "ok")
        return Redirect(f"/?clamp_id={clamp.id}")


class ClampController(Controller):
    path = "/clamps"
    tags = ["clamps"]

    @post("/{clamp_id:int}/status")
    async def set_status(
        self,
        request: Request,
        clamp_id: int,
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
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


class CertificateController(Controller):
    path = "/certificates"
    tags = ["certificates"]

    @post("/new")
    async def issue_certificate(
        self,
        request: Request,
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        """操作工可办证；同窑未作废证最多一张（数据库唯一索引兜底并发争抢）。"""
        if not request.user:
            return Redirect("/login")
        clamp_id = _parse_optional_int(data.get("clamp_id"))
        volume = _parse_optional_float(data.get("volume_m3"))
        limit = _parse_optional_float(data.get("temp_limit_c"))
        effective_date = _parse_positive_date(data.get("effective_date"))
        if clamp_id is None:
            _set_flash(request, "请选择炭窑", "error")
            return Redirect("/certificates")
        try:
            assert_valid_certificate_fields(volume if volume is not None else 0, limit if limit is not None else 0)
        except RuleError as exc:
            _set_flash(request, str(exc), "error")
            return Redirect("/certificates")
        async with SessionLocal() as db:
            clamp = (
                await db.execute(select(Clamp).where(Clamp.id == clamp_id))
            ).scalar_one_or_none()
            if not clamp:
                _set_flash(request, "炭窑不存在", "error")
                return Redirect("/certificates")
            clamp_code = clamp.code
            cert = VolumeCertificate(
                clamp_id=clamp_id,
                volume_m3=volume,
                temp_limit_c=limit,
                effective_date=effective_date,
                issued_by=request.user.username,
            )
            db.add(cert)
            try:
                await db.commit()
            except IntegrityError:
                # 两名操作工并发争抢时，唯一索引只放一张，另一张在此被挡下。
                # rollback 会使对象过期，clamp.code 须在回滚前取好，避免异步惰性刷新。
                await db.rollback()
                _set_flash(request, f"窑 {clamp_code} 已有未作废容积证，新证登记被挡下", "error")
                return Redirect("/certificates")
        _set_flash(request, f"窑 {clamp_code} 的窑膛容积证已签发", "ok")
        return Redirect("/certificates")

    @post("/{cert_id:int}/revoke")
    async def revoke_certificate(
        self,
        request: Request,
        cert_id: int,
        data: dict[str, Any] = Body(media_type=RequestEncodingType.URL_ENCODED),
    ) -> Redirect:
        """作废权只给管理员。作废后该证不再约束，可为同窑再办新证。"""
        if not request.user:
            return Redirect("/login")
        if not _is_admin(request):
            _set_flash(request, "只有管理员可以作废容积证", "error")
            return Redirect("/certificates")
        async with SessionLocal() as db:
            cert = (
                await db.execute(select(VolumeCertificate).where(VolumeCertificate.id == cert_id))
            ).scalar_one_or_none()
            if not cert:
                return Redirect("/certificates")
            if cert.revoked_at is None:
                cert.revoked_at = utcnow()
                await db.commit()
                _set_flash(request, "容积证已作废，不再约束班次峰值", "ok")
            else:
                _set_flash(request, "该证本已作废", "error")
        return Redirect("/certificates")
