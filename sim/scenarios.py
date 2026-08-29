"""验证场景定义与运行辅助（docs/simulation-plan.md 第 2.7 节）。"""

from __future__ import annotations

from .models import Weather, House, Device, Thermostat
from .algorithms import FixedSpeedBaseline, FixedTes, V144Learner
from .simulator import run, Result


def make_algorithm(kind: str, **kw):
    if kind == "A":
        return FixedSpeedBaseline()
    if kind == "B":
        return FixedTes(tes=kw.get("tes", 7.0))
    if kind == "C":
        return V144Learner(**kw)
    raise ValueError(kind)


def make_stack(kind: str, weather: Weather | None = None,
               deadband_f: float = 1.0, meas_bias: float = 0.0,
               t_set: float = 24.5, **algo_kw):
    """构建一套（天气、房屋、设备、温控器、算法）。"""
    weather = weather or Weather()
    house = House(t_room=t_set + 1.5, t_mass=t_set + 0.5)
    device = Device(variable=(kind != "A"), meas_bias=meas_bias)
    thermostat = Thermostat(t_set=t_set, deadband=deadband_f * 5.0 / 9.0)
    algo = make_algorithm(kind, **algo_kw)
    return weather, house, device, thermostat, algo


def run_scenario(kind: str, days: float = 3.0, record_series: bool = False,
                 **kw) -> Result:
    weather, house, device, thermostat, algo = make_stack(kind, **kw)
    res = run(algo, weather, house, device, thermostat,
              days=days, record_series=record_series)
    res.algo = algo
    return res


# ---- 预置场景 ----

def s1_design_day(kind: str, **kw) -> Result:
    """S1 设计日稳态：恒定 35℃，V1.44 预学习完成。"""
    w = Weather(t_mean=35.0, t_amp=0.0, solar_peak_w=2000.0)
    if kind == "C":
        kw.setdefault("prelearn_q", None)  # 由 run_validation 注入标定值
    return run_scenario(kind, days=3.0, weather=w, **kw)


def s2_summer_week(kind: str, **kw) -> Result:
    """S2 典型夏周：30±5℃ × 7 天，冷启动学习。"""
    w = Weather(t_mean=30.0, t_amp=5.0)
    return run_scenario(kind, days=7.0, weather=w, **kw)


def s3_mild_season(kind: str, **kw) -> Result:
    """S3 轻负荷季：24±4℃。"""
    w = Weather(t_mean=24.0, t_amp=4.0, solar_peak_w=1800.0)
    return run_scenario(kind, days=3.0, weather=w, t_set=23.0, **kw)


def s4_setpoint_step(kind: str, **kw) -> Result:
    """S4 设定阶跃：稳态运行 1 天后 T_set 从 25.5→24.0℃，观察 pull-down。"""
    weather = Weather(t_mean=32.0, t_amp=4.0)
    weather_, house, device, thermostat, algo = make_stack(
        kind, weather=weather, t_set=25.5, **kw)
    r1 = run(algo, weather, house, device, thermostat, days=1.0)
    thermostat.t_set = 24.0
    r2 = run(algo, weather, house, device, thermostat, days=1.0,
             t_start=12 * 3600.0 + 1 * 86400.0, record_series=True)
    r2.name = algo.name
    r2.algo = algo
    return r2
