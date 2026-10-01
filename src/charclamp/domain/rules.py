"""炭窑焖烧志业务规则。"""

from __future__ import annotations

from charclamp.domain.models import BurnShift, Clamp, VolumeCertificate

MIN_PEAK_TEMP_FOR_DRAWN = 400.0

NO_CERT_MSG = "该窑尚无现行窑膛容积证，请先办证后再登记班次峰值"


class RuleError(ValueError):
    """业务规则校验失败。"""


def latest_shift_for_clamp(clamp: Clamp) -> BurnShift | None:
    if not clamp.shifts:
        return None
    return max(clamp.shifts, key=lambda s: s.started_at)


def assert_valid_certificate_fields(volume_m3: float, temp_limit_c: float) -> None:
    """容积与温度上限都必须为正。"""
    if volume_m3 is None or volume_m3 <= 0:
        raise RuleError("窑膛容积（立方米）必须为正数")
    if temp_limit_c is None or temp_limit_c <= 0:
        raise RuleError("温度上限（摄氏）必须为正数")


def over_limit_message(cert: VolumeCertificate, peak_temp_c: float) -> str:
    return (
        f"峰值温度 {peak_temp_c:g}℃ 超过该窑现行容积证（{cert.volume_m3:g} 立方米）"
        f"温度上限 {cert.temp_limit_c:g}℃"
    )


def assert_peak_allowed(clamp: Clamp, peak_temp_c: float | None) -> VolumeCertificate:
    """
    登记或改写班次峰值时：
    1. 必须读取该窑最新未作废证——无证则拒绝并提示先办证；
    2. 峰值若填写则不得超过证面温度上限。

    抽屉提交与保存接口共用本函数，保证同一句中文。
    返回命中的现行证。
    """
    cert = clamp.active_certificate() if hasattr(clamp, "active_certificate") else None
    if cert is None:
        raise RuleError(NO_CERT_MSG)
    if peak_temp_c is not None and peak_temp_c > cert.temp_limit_c:
        raise RuleError(over_limit_message(cert, peak_temp_c))
    return cert


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
