"""仿真引擎：组装子模型，推进时间，采集循环事件与指标。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import Weather, House, Device, Thermostat
from .algorithms import Algorithm


@dataclass
class Cycle:
    """一个完整开停循环（thermo-on → thermo-off → 下一次 thermo-on）。"""

    t_on: float          # thermo-on 时刻 s
    t2: float = 0.0      # 运转时长 s（达温时间）
    t1: float = 0.0      # 其后的停机时长 s
    energy_j: float = 0.0


@dataclass
class Result:
    name: str
    days: float
    pulldown_s: float | None      # 初始达温时间（首次 thermo-off）
    cycles: list = field(default_factory=list)
    energy_kwh: float = 0.0
    cool_kwh: float = 0.0         # 显热制冷量（用于 COP）
    rmse_c: float = 0.0           # 室温对设定的均方根偏差
    out_of_band_frac: float = 0.0
    series: dict | None = None    # 可选时间序列（降采样）

    @property
    def complete_cycles(self):
        return [c for c in self.cycles if c.t1 > 0]

    def summary(self) -> dict:
        cc = self.complete_cycles
        t2s = [c.t2 / 60 for c in self.cycles if c.t2 > 0]
        t1s = [c.t1 / 60 for c in cc]
        mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")
        return {
            "算法": self.name,
            "初始达温 min": round(self.pulldown_s / 60, 1) if self.pulldown_s else None,
            "平均运转 t2 min": round(mean(t2s), 1),
            "平均停机 t1 min": round(mean(t1s), 1),
            "循环次数/天": round(len(self.cycles) / self.days, 1),
            "日耗电 kWh": round(self.energy_kwh / self.days, 2),
            "平均COP(显热)": round(self.cool_kwh / self.energy_kwh, 2)
            if self.energy_kwh > 0 else None,
            "室温RMSE ℃": round(self.rmse_c, 2),
            "出带时间 %": round(self.out_of_band_frac * 100, 1),
        }


def run(algorithm: Algorithm, weather: Weather, house: House, device: Device,
        thermostat: Thermostat, days: float = 3.0, dt: float = 10.0,
        record_series: bool = False, series_step: float = 60.0,
        t_start: float = 12 * 3600.0) -> Result:
    """运行仿真。t_start 默认从正午开始（进入高负荷时段）。"""

    t_end = t_start + days * 86400.0
    t = t_start
    res = Result(name=algorithm.name, days=days, pulldown_s=None)
    if record_series:
        res.series = {k: [] for k in
                      ("t", "t_room", "t_out", "q_ac", "power", "tes", "call")}

    sq_err = 0.0
    n_err = 0
    oob = 0
    cur: Cycle | None = None
    call_prev = False
    next_series = t

    while t < t_end:
        t_out = weather.outdoor_temp(t)
        q_solar = weather.solar_gain(t)

        call = thermostat.step(t, house.t_room)
        q_meas = device.measured_capacity() if call else 0.0
        tes = algorithm.update(t, dt, call, t_out, q_meas)
        q_sens, power = device.step(dt, call, t_out, tes)
        house.step(dt, t_out, q_solar, q_sens)

        # 循环事件
        if call and not call_prev:
            cur = Cycle(t_on=t)
            res.cycles.append(cur)
        elif not call and call_prev:
            if cur is not None:
                cur.t2 = t - cur.t_on
            if res.pulldown_s is None:
                res.pulldown_s = t - t_start
        if not call and cur is not None and cur.t2 > 0:
            cur.t1 = t - (cur.t_on + cur.t2)
        call_prev = call

        # 能耗与舒适度
        res.energy_kwh += power * dt / 3.6e6
        res.cool_kwh += q_sens * dt / 3.6e6
        if cur is not None and call:
            cur.energy_j += power * dt
        err = house.t_room - thermostat.t_set
        sq_err += err * err
        n_err += 1
        if abs(err) > thermostat.deadband:
            oob += 1

        if record_series and t >= next_series:
            s = res.series
            s["t"].append(round(t, 1))
            s["t_room"].append(round(house.t_room, 3))
            s["t_out"].append(round(t_out, 2))
            s["q_ac"].append(round(q_sens, 0))
            s["power"].append(round(power, 0))
            s["tes"].append(round(tes, 2))
            s["call"].append(1 if call else 0)
            next_series = t + series_step

        t += dt

    res.rmse_c = math.sqrt(sq_err / max(n_err, 1))
    res.out_of_band_frac = oob / max(n_err, 1)
    return res
