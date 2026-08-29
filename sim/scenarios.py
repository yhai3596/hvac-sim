"""场景构建：地点（ASHRAE 气候）× 季节 × 保温档 × 设备/算法参数。"""

from __future__ import annotations

from .climate import (LOCATIONS, INSULATION_LEVELS, scenario_params,
                      w_from_dew)
from .models import Weather, House, Device, Thermostat, HFG, RHO_AIR
from .algorithms import FixedSpeedBaseline, FixedTarget, V144Learner
from .simulator import run, Result

DEFAULT_TSET = {"cooling": 24.5, "heating": 21.1}


def make_algorithm(kind: str, mode: str, **kw):
    if kind == "A":
        return FixedSpeedBaseline(mode=mode)
    if kind == "B":
        return FixedTarget(mode=mode, target=kw.get("target"))
    if kind == "C":
        return V144Learner(mode=mode, **kw)
    raise ValueError(kind)


def design_load(loc_key: str, insulation: str = "medium",
                t_set: float | None = None) -> dict:
    """地点设计负荷估算（用于容量配比显示与预学习初值）。"""
    loc = LOCATIONS[loc_key]
    lvl = INSULATION_LEVELS[insulation]
    t_set = t_set if t_set is not None else DEFAULT_TSET["cooling"]
    sens = lvl["ua"] * (loc.summer_db04 - t_set) + 0.5 * loc.solar_peak_w + 300.0
    vol = 160.0 * 2.7
    m_inf = lvl["ach"] * vol * RHO_AIR / 3600.0
    w_in = 0.0104   # 24.5℃/55%RH 左右
    lat = max(0.0, m_inf * (w_from_dew(loc.summer_dew) - w_in) * HFG)
    return {"sensible_w": sens, "latent_w": lat, "total_w": sens + lat,
            "heating_w": max(0.0, lvl["ua"] * (21.1 - loc.winter_db996))}


TON_W = 3517.0


def make_stack(kind: str, location: str, scenario: str = "summer_design",
               insulation: str = "medium", t_set: float | None = None,
               deadband_f: float = 1.0, meas_bias: float = 0.0,
               cfm_per_ton: float = 400.0, fan_mode: str = "auto",
               tons: float = 3.0, q_rated: float | None = None,
               satisfaction: float | None = None,
               fan_ctrl: str = "fixed", two_stage: int = 2,
               auto_init: float = 0.60, auto_wait_s: float = 3000.0,
               auto_step_s: float = 600.0, auto_step: float = 0.10,
               ramp_hz_s: float = 0.5,
               start_hold_s: float = 180.0, aux_enabled: bool = True,
               **algo_kw):
    """构建一套（天气、房屋、设备、温控器、算法）。

    tons：能力段（3 或 5 冷吨）；q_rated 显式给定时优先。
    satisfaction：负荷满足度 = 额定能力/建筑设计负荷（0.5~1.5）。给定时按当前
    模式的设计负荷锚定，把所有负荷源（UA/ACH/日照/内扰显热/产湿）等比缩放，
    使 满足度 精确成立；房屋参数仍决定负荷"构成"，满足度决定负荷"总量"。
    """
    loc = LOCATIONS[location]
    sp = scenario_params(loc, scenario)
    mode = sp["mode"]
    t_set = t_set if t_set is not None else DEFAULT_TSET[mode]
    q_rated = q_rated if q_rated is not None else tons * TON_W
    lvl = INSULATION_LEVELS[insulation]
    load_scale = 1.0
    if satisfaction is not None:
        base = design_load(location, insulation)
        anchor = base["total_w"] if mode == "cooling" else base["heating_w"]
        cap = q_rated if mode == "cooling" else q_rated * 1.09
        load_scale = cap / (satisfaction * max(anchor, 1.0))
    weather = Weather(t_mean=sp["t_mean"], t_amp=sp["t_amp"],
                      dew_point=sp["dew_point"],
                      solar_peak_w=sp["solar_peak_w"] * load_scale)
    if mode == "cooling":
        t0, m0 = t_set + 1.5, t_set + 0.5
        w0 = w_from_dew(min(sp["dew_point"], t_set - 3.0))
    else:
        t0, m0 = t_set - 1.5, t_set - 0.5
        w0 = w_from_dew(2.0)
    house = House(ua=lvl["ua"] * load_scale, ach=lvl["ach"] * load_scale,
                  q_internal=300.0 * load_scale,
                  moist_gain_kgh=0.30 * load_scale,
                  t_room=t0, t_mass=m0, w_room=w0)
    device = Device(variable=(kind != "A"), meas_bias=meas_bias,
                    cfm_per_ton=cfm_per_ton, fan_mode=fan_mode,
                    fan_ctrl=fan_ctrl, two_stage=two_stage,
                    auto_init=auto_init, auto_wait_s=auto_wait_s,
                    auto_step_s=auto_step_s, auto_step=auto_step,
                    q_rated=q_rated, q_rated_heat=q_rated * 1.09,
                    ramp_hz_s=ramp_hz_s, start_hold_s=start_hold_s,
                    aux_enabled=aux_enabled)
    thermostat = Thermostat(t_set=t_set, deadband=deadband_f * 5.0 / 9.0)
    algo = make_algorithm(kind, mode, **algo_kw)
    return weather, house, device, thermostat, algo


def run_scenario(kind: str, location: str, scenario: str = "summer_design",
                 days: float = 3.0, record_series: bool = False,
                 **kw) -> Result:
    weather, house, device, thermostat, algo = make_stack(
        kind, location, scenario, **kw)
    res = run(algo, weather, house, device, thermostat,
              days=days, record_series=record_series)
    res.algo = algo
    return res


def run_setpoint_step(kind: str, location: str, step_c: float = 1.5,
                      days_pre: float = 1.0, days_post: float = 1.0,
                      scenario: str = "summer_design", **kw) -> Result:
    """稳态运行后设定温度下调 step_c，观察 pull-down。"""
    weather, house, device, thermostat, algo = make_stack(
        kind, location, scenario, **kw)
    thermostat.t_set += step_c          # 先在较高设定稳态
    house.t_room = thermostat.t_set + 1.5
    house.t_mass = thermostat.t_set + 0.5
    run(algo, weather, house, device, thermostat, days=days_pre)
    thermostat.t_set -= step_c
    res = run(algo, weather, house, device, thermostat, days=days_post,
              t_start=12 * 3600.0 + days_pre * 86400.0)
    res.name = algo.name
    res.algo = algo
    return res
