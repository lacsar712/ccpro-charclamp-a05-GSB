"""炭窑焖烧志业务规则。"""

from __future__ import annotations

from charclamp.domain.models import BurnShift, Clamp, VolumeCertificate

MIN_PEAK_TEMP_FOR_DRAWN = 400.0

# 抽屉提交与保存接口共用的同一句中文提示。
ERR_NO_CERT = "该炭窑尚无现行窑膛容积证，请先办证后再登记班次峰值"


class RuleError(ValueError):
    """业务规则校验失败。"""


def latest_shift_for_clamp(clamp: Clamp) -> BurnShift | None:
    if not clamp.shifts:
        return None
    return max(clamp.shifts, key=lambda s: s.started_at)


def active_certificate(clamp: Clamp) -> VolumeCertificate | None:
    """该窑最新一张未作废的窑膛容积证；无则 None。"""
    active = [c for c in clamp.certificates if c.revoked_at is None]
    if not active:
        return None
    return max(active, key=lambda c: (c.effective_date, c.id))


def assert_peak_allowed_by_certificate(clamp: Clamp, peak_temp_c: float | None) -> None:
    """
    登记或改写班次峰值时：
    - 该窑必须有现行（未作废）容积证，否则拒绝；
    - 峰值若已填写，不得超过证面温度上限。
    未填写峰值时只检查证件是否存在。
    """
    cert = active_certificate(clamp)
    if cert is None:
        raise RuleError(ERR_NO_CERT)
    if peak_temp_c is not None and peak_temp_c > cert.temp_limit_c:
        raise RuleError(
            f"峰值温度 {peak_temp_c:g}℃ 超过该窑现行容积证温度上限 {cert.temp_limit_c:g}℃，不能登记"
        )


def can_mark_clamp_drawn(clamp: Clamp) -> tuple[bool, str]:
    """
    炭窑转为「已出炭」(drawn) 的前提：
    最近一条焖烧班次的峰值温度已记录，且 >= 400℃。
    """
    latest = latest_shift_for_clamp(clamp)
    if latest is None:
        return False, "该窑尚无焖烧班次，不能标记为已出炭"
    if latest.peak_temp_c is None:
        return False, "最近班次尚未记录峰值温度，不能标记为已出炭"
    if latest.peak_temp_c < MIN_PEAK_TEMP_FOR_DRAWN:
        return (
            False,
            f"最近班次峰值温度 {latest.peak_temp_c}℃ 低于 {MIN_PEAK_TEMP_FOR_DRAWN:.0f}℃，不能标记为已出炭",
        )
    return True, ""


def assert_can_set_clamp_status(clamp: Clamp, new_status: str) -> None:
    allowed = {Clamp.STATUS_STACKED, Clamp.STATUS_BURNING, Clamp.STATUS_DRAWN}
    if new_status not in allowed:
        raise RuleError(f"无效状态：{new_status}")
    if new_status == Clamp.STATUS_DRAWN:
        ok, msg = can_mark_clamp_drawn(clamp)
        if not ok:
            raise RuleError(msg)
