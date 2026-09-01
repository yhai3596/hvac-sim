"""仿真引擎 v2：组装子模型，推进时间，采集循环事件与指标（含湿度/频率）。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import Weather, House, Device, Thermostat, HFG
from .climate import rh_from_w
from .algorithms import Algorithm


@dataclass
class Cycle:
    """一个完整开停循环（thermo-on → thermo-off → 下一次 thermo-on）。"""

    t_on: float
    t2: float = 0.0        # 运转时长 s（达温时间）
    t1: float = 0.0        # 其后的停机时长 s
    energy_j: float = 0.0
    hz_peak: float = 0.0
    hz95_s: float | None = None   # thermo-on 至频率达到本循环峰值 95% 的时间
    hwm: list = field(default_factory=list)   # 频率高水位标记 (相对时刻, Hz)


@dataclass
class Result:
    name: str
    mode: str
    days: float
    pulldown_s: float | None
    cycles: list = field(default_factory=list)
    energy_kwh: float = 0.0
    aux_kwh: float = 0.0
    cool_kwh: float = 0.0         # 显热移出（制冷）/注入（制热，取绝对值）
    latent_kwh: float = 0.0
    dehum_kg: float = 0.0         # 净除湿量（凝水排走 = 除湿 − 再蒸发）
    rmse_c: float = 0.0
    out_of_band_frac: float = 0.0
    rh_mean: float = 0.0
    rh_over60_frac: float = 0.0
    defrost_count: int = 0
    series: dict | None = None

    def summary(self) -> dict:
        done = [c for c in self.cycles if c.t2 > 0]
        offc = [c for c in self.cycles if c.t1 > 0]
        hzs = [c.hz95_s / 60 for c in done if c.hz95_s is not None]
        mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")
        r2 = lambda v: round(v, 2) if v == v else None
        out = {
            "算法": self.name,
            "初始达温 min": round(self.pulldown_s / 60, 1) if self.pulldown_s else None,
            "平均运转 t2 min": r2(mean([c.t2 / 60 for c in done])),
            "平均停机 t1 min": r2(mean([c.t1 / 60 for c in offc])),
            "达温间隔 min": r2(mean([(c.t2 + c.t1) / 60 for c in offc])),
            "压缩机到峰值 min": r2(mean(hzs)),
            "循环次数/天": round(len(self.cycles) / self.days, 1),
            "日耗电 kWh": round(self.energy_kwh / self.days, 2),
            "室温RMSE ℃": round(self.rmse_c, 2),
            "出带时间 %": round(self.out_of_band_frac * 100, 1),
        }
        if self.mode == "cooling":
            out["RH均值 %"] = round(self.rh_mean * 100, 1)
            out["RH>60% 时间"] = f"{self.rh_over60_frac * 100:.0f}%"
            out["除湿 L/天"] = round(self.dehum_kg / self.days, 1)
        else:
            out["化霜次数/天"] = round(self.defrost_count / self.days, 1)
            out["辅热 kWh/天"] = round(self.aux_kwh / self.days, 2)
        return out


def run(algorithm: Algorithm, weather: Weather, house: House, device: Device,
        thermostat: Thermostat, days: float = 3.0, dt: float = 10.0,
        record_series: bool = False, series_step: float = 60.0,
        t_start: float = 12 * 3600.0) -> Result:
    mode = algorithm.mode
    t_end = t_start + days * 86400.0
    t = t_start
    res = Result(name=algorithm.name, mode=mode, days=days, pulldown_s=None)
    if record_series:
        res.series = {k: [] for k in ("t", "t_room", "t_out", "q_ac", "power",
                                      "tgt", "call", "rh", "hz")}
    w_out = weather.outdoor_w()
    sq = 0.0
    n = 0
    oob = 0
    rh_sum = 0.0
    rh_over = 0
    cur: Cycle | None = None
    call_prev = False
    defrost_prev = False
    next_series = t

    while t < t_end:
        t_out = weather.outdoor_temp(t)
        q_solar = weather.solar_gain(t)

        call = thermostat.step(t, house.t_room, mode)
        q_meas = device.measured_capacity() if call else 0.0
        tgt = algorithm.update(t, dt, call, t_out, q_meas)
        out = device.step(dt, mode, call, t_out, w_out, tgt,
                          house.t_room, house.w_room, thermostat.t_set)
        house.step(dt, t_out, w_out, q_solar,
                   out["q_sens"], out["latent_w"], out["reevap_w"])

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
        if call and cur is not None and device.f_hz > cur.hz_peak:
            cur.hz_peak = device.f_hz
            cur.hwm.append((t - cur.t_on, device.f_hz))
        call_prev = call

        # 化霜计数
        defrosting = device.defrost_left > 0
        if defrosting and not defrost_prev:
            res.defrost_count += 1
        defrost_prev = defrosting

        # 能耗/湿度/舒适度
        power = out["power"] + out["aux_w"]
        res.energy_kwh += power * dt / 3.6e6
        res.aux_kwh += out["aux_w"] * dt / 3.6e6
        res.cool_kwh += abs(out["q_sens"]) * dt / 3.6e6
        res.latent_kwh += out["latent_w"] * dt / 3.6e6
        res.dehum_kg += (out["latent_w"] - out["reevap_w"]) / HFG * dt
        if cur is not None and call:
            cur.energy_j += power * dt
        err = house.t_room - thermostat.t_set
        sq += err * err
        n += 1
        if abs(err) > thermostat.deadband:
            oob += 1
        rh = rh_from_w(house.t_room, house.w_room)
        rh_sum += rh
        if rh > 0.60:
            rh_over += 1

        if record_series and t >= next_series:
            s = res.series
            s["t"].append(round(t, 1))
            s["t_room"].append(round(house.t_room, 3))
            s["t_out"].append(round(t_out, 2))
            s["q_ac"].append(round(out["q_sens"], 0))
            s["power"].append(round(power, 0))
            s["tgt"].append(round(tgt, 2))
            s["call"].append(1 if call else 0)
            s["rh"].append(round(rh * 100, 1))
            s["hz"].append(round(device.f_hz, 1))
            next_series = t + series_step

        t += dt

    # 结算各循环"压缩机到峰值 95%"时间（高水位标记回扫）
    for c in res.cycles:
        if c.t2 > 0 and c.hz_peak > 0:
            thresh = 0.95 * c.hz_peak
            c.hz95_s = next((rt for rt, hz in c.hwm if hz >= thresh), None)

    res.rmse_c = math.sqrt(sq / max(n, 1))
    res.out_of_band_frac = oob / max(n, 1)
    res.rh_mean = rh_sum / max(n, 1)
    res.rh_over60_frac = rh_over / max(n, 1)
    return res
