"""物理子模型 v2：天气（含湿度）、房间（2R2C + 湿平衡）、变频热泵设备
（显式压缩机频率 + 风量 + ADP/旁通因子显热潜热拆分 + 化霜/辅热）、温控器。

单位约定：温度 ℃，功率 W，能量 J，时间 s，含湿量 kg/kg。
模型依据见 docs/simulation-plan.md；气候与湿空气性质见 sim/climate.py。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .climate import p_sat, w_from_dew

DAY_S = 86400.0
HFG = 2.45e6          # 水汽化潜热 J/kg
RHO_AIR = 1.2         # kg/m3
CP_AIR = 1006.0       # J/(kg·K)
TON_W = 3517.0        # 1 冷吨 = 3517 W


def w_sat(t_c: float) -> float:
    pv = p_sat(t_c)
    return 0.622 * pv / (101325.0 - pv)


def enthalpy(t_c: float, w: float) -> float:
    """湿空气焓 J/kg(干空气)。"""
    return 1006.0 * t_c + w * (2.501e6 + 1860.0 * t_c)


@dataclass
class Weather:
    """室外干球：日周期余弦（峰值 15:00）；露点按日恒定；日照白天正弦包络。"""

    t_mean: float = 30.0
    t_amp: float = 5.0
    dew_point: float = 18.0
    solar_peak_w: float = 2500.0
    t_peak_hour: float = 15.0
    sunrise_hour: float = 6.5
    sunset_hour: float = 19.5

    def outdoor_temp(self, t: float) -> float:
        phase = 2.0 * math.pi * (t / DAY_S - self.t_peak_hour / 24.0)
        return self.t_mean + self.t_amp * math.cos(phase)

    def outdoor_w(self) -> float:
        return w_from_dew(self.dew_point)

    def solar_gain(self, t: float) -> float:
        hour = (t % DAY_S) / 3600.0
        if hour <= self.sunrise_hour or hour >= self.sunset_hour:
            return 0.0
        frac = (hour - self.sunrise_hour) / (self.sunset_hour - self.sunrise_hour)
        return self.solar_peak_w * math.sin(math.pi * frac)


@dataclass
class House:
    """2R2C 集总热模型 + 单区湿平衡。"""

    ua: float = 350.0            # 围护+渗透显热综合传热 W/K
    c_air: float = 3.0e6         # J/K
    c_mass: float = 40.0e6       # J/K
    h_mass: float = 500.0        # W/K
    f_air: float = 0.4
    q_internal: float = 300.0    # 内扰显热 W
    area_m2: float = 160.0       # 建筑面积（湿平衡空气体积用）
    ach: float = 0.6             # 渗透换气次数 1/h
    moist_gain_kgh: float = 0.30 # 内扰产湿 kg/h
    moist_cap_mult: float = 15.0 # 湿容量放大（家具/织物缓冲）
    t_room: float = 26.0
    t_mass: float = 25.0
    w_room: float = 0.010        # 含湿量 kg/kg

    @property
    def air_mass(self) -> float:
        return RHO_AIR * self.area_m2 * 2.7

    def step(self, dt: float, t_out: float, w_out: float, q_solar: float,
             q_ac_sens: float, latent_removal_w: float, reevap_w: float) -> None:
        """q_ac_sens>0 制冷移出热量，<0 制热注入；latent_removal_w 盘管除湿功率，
        reevap_w 盘管水分再蒸发回房间的潜热功率。"""
        gains = q_solar + self.q_internal
        dq_air = (self.ua * (t_out - self.t_room)
                  + self.h_mass * (self.t_mass - self.t_room)
                  + self.f_air * gains - q_ac_sens)
        dq_mass = self.h_mass * (self.t_room - self.t_mass) + (1.0 - self.f_air) * gains
        self.t_room += dq_air / self.c_air * dt
        self.t_mass += dq_mass / self.c_mass * dt
        # 湿平衡
        m_inf = self.ach * self.area_m2 * 2.7 * RHO_AIR / 3600.0   # kg/s
        dw = (m_inf * (w_out - self.w_room)
              + self.moist_gain_kgh / 3600.0
              - latent_removal_w / HFG
              + reevap_w / HFG) / (self.air_mass * self.moist_cap_mult)
        self.w_room = max(0.001, self.w_room + dw * dt)


@dataclass
class Device:
    """变频热泵外机 + 跨品牌内机风量匹配。

    压缩机为显式频率状态：启动低频保持（回油）→ 斜率受限的积分控制追踪
    Te→Tes（制热为 Tc→Tcs）。能力潜显拆分用 ADP/旁通因子法。
    定频基线机型 variable=False：thermo-on 即 60Hz。
    """

    # 额定点：制冷 35℃/60Hz/400CFM每冷吨/Te=7℃；制热 8.3℃/60Hz
    q_rated: float = 7000.0
    cop_rated: float = 3.3
    q_rated_heat: float = 7600.0
    cop_rated_heat: float = 3.6
    cop_fixed: float = 3.0            # 定频基线制冷 COP
    # 压缩机
    f_min: float = 20.0
    f_max: float = 65.0
    f_rated: float = 60.0
    f_start: float = 30.0             # 启动频率 Hz
    start_hold_s: float = 180.0       # 启动低频保持（回油）s
    ramp_hz_s: float = 0.5            # 最大爬升速率 Hz/s
    ki: float = 0.02                  # 积分增益 Hz/s 每 K 偏差
    # 内机风量
    cfm_per_ton: float = 400.0        # 匹配风量（外部条件或配套可控）
    fan_w_per_cfm: float = 0.35
    fan_mode: str = "auto"            # auto=停机延迟 90s 关风机 / on=常开
    bf_400: float = 0.15              # 400 CFM/ton 时旁通因子
    # 性能曲线斜率
    kq_oat_c: float = 0.010           # 制冷：室外温度每降 1℃ 能力增益
    ke_oat_c: float = 0.020
    kq_oat_h: float = 0.022           # 制热：室外温度每升 1℃ 能力增益
    ke_oat_h: float = 0.090           # 制热 COP 随室外温度斜率（绝对值/℃）
    dt_coil_c: float = 19.7           # 额定工况室温-蒸发温差
    dt_coil_h_off: float = 10.0       # 制热 Tc 高于室温的基础偏移
    dt_coil_h_k: float = 14.0
    tau_hx: float = 45.0              # 换热器一阶时间常数 s
    # 化霜与辅热
    defrost_run_interval_s: float = 3000.0   # 湿冷工况累计运转触发化霜
    defrost_duration_s: float = 480.0
    aux_heat_w: float = 8000.0               # 北美常见 8~10kW 电辅热条
    aux_enabled: bool = True
    # 测量
    meas_bias: float = 0.0
    variable: bool = True

    # ---- 状态 ----
    f_hz: float = field(default=0.0, repr=False)
    q_del: float = field(default=0.0, repr=False)      # 交付总能力 W（含惯性）
    run_t: float = field(default=0.0, repr=False)
    off_t: float = field(default=1e9, repr=False)
    coil_water: float = field(default=0.0, repr=False)  # 盘管持水 kg
    coil_water_max: float = 0.40
    frost_accum: float = field(default=0.0, repr=False)
    defrost_left: float = field(default=0.0, repr=False)
    aux_on: bool = field(default=False, repr=False)
    # 每步输出（供采集）
    te: float = field(default=7.0, repr=False)
    tc: float = field(default=40.0, repr=False)
    shr: float = field(default=1.0, repr=False)

    @property
    def flow_factor(self) -> float:
        return self.cfm_per_ton / 400.0

    @property
    def cap_ff(self) -> float:
        return 1.0 + 0.35 * (self.flow_factor - 1.0)

    def fan_power(self) -> float:
        tons = self.q_rated / TON_W
        return self.cfm_per_ton * tons * self.fan_w_per_cfm

    # ---- 主步进 ----
    def step(self, dt: float, mode: str, call: bool, t_out: float, w_out: float,
             target: float, t_room: float, w_room: float, t_set: float):
        """返回 dict：q_sens（移出为正/注入为负）、latent_w、reevap_w、power、aux_w。"""
        if mode == "cooling":
            return self._step_cool(dt, call, t_out, target, t_room, w_room)
        return self._step_heat(dt, call, t_out, w_out, target, t_room, t_set)

    # ---- 制冷 ----
    def _step_cool(self, dt, call, t_out, tes, t_room, w_room):
        fan_on = call
        reevap = 0.0
        if call:
            self.off_t = 0.0
            if self.f_hz <= 0.0:
                self.f_hz = self.f_start
                self.run_t = 0.0
            self.run_t += dt
            cap_ft = 1.0 + self.kq_oat_c * (35.0 - t_out)
            q_ss = self.q_rated * (self.f_hz / self.f_rated) ** 0.95 * cap_ft * self.cap_ff
            self.te = t_room - self.dt_coil_c * (q_ss / self.q_rated) / self.flow_factor
            if self.variable:
                if self.run_t >= self.start_hold_s:
                    df = self.ki * (self.te - tes) * dt
                    df = max(-self.ramp_hz_s * dt, min(self.ramp_hz_s * dt, df))
                    self.f_hz = max(self.f_min, min(self.f_max, self.f_hz + df))
            else:
                self.f_hz = min(self.f_rated,
                                self.f_hz + self.ramp_hz_s * 2.0 * dt)  # 定频直接上满
            self.q_del += (q_ss - self.q_del) / self.tau_hx * dt
            # ADP/旁通因子 潜显拆分
            adp = self.te + 2.0
            w_adp = w_sat(adp)
            if w_adp >= w_room:
                self.shr = 1.0
            else:
                bf = self.bf_400 ** (1.0 / self.flow_factor)
                t_sup = adp + bf * (t_room - adp)
                w_sup = w_adp + bf * (w_room - w_adp)
                dh = enthalpy(t_room, w_room) - enthalpy(t_sup, w_sup)
                self.shr = min(1.0, max(0.3, CP_AIR * (t_room - t_sup) / max(dh, 1.0)))
            q_sens = self.q_del * self.shr
            q_lat = self.q_del * (1.0 - self.shr)
            self.coil_water = min(self.coil_water_max,
                                  self.coil_water + q_lat / HFG * dt)  # 超出部分排走
            cop = (self.cop_rated if self.variable else self.cop_fixed)
            cop *= (1.0 + self.ke_oat_c * (35.0 - t_out))
            if self.variable:
                cop *= (1.0 + 0.028 * (self.te - 7.0))
                cop *= max(0.9, min(1.12, 1.0 + 0.10 * (1.0 - self.f_hz / self.f_rated)))
            power = q_ss / max(cop, 0.5) + self.fan_power()
            return {"q_sens": q_sens, "latent_w": q_lat, "reevap_w": 0.0,
                    "power": power, "aux_w": 0.0}
        # 停机段
        self.f_hz = 0.0
        self.run_t = 0.0
        self.off_t += dt
        self.q_del += (0.0 - self.q_del) / self.tau_hx * dt
        if self.q_del < 1.0:
            self.q_del = 0.0
        fan_running = (self.fan_mode == "on") or (self.off_t <= 90.0)
        if fan_running and self.coil_water > 1e-4:
            rate = self.coil_water / (600.0 if self.fan_mode == "on" else 90.0)
            rate = min(rate, self.coil_water / dt)
            self.coil_water -= rate * dt
            reevap = rate * HFG
        power = self.fan_power() if fan_running else 0.0
        return {"q_sens": 0.0, "latent_w": 0.0, "reevap_w": reevap,
                "power": power, "aux_w": 0.0}

    # ---- 制热 ----
    def _step_heat(self, dt, call, t_out, w_out, tcs, t_room, t_set):
        if not call:
            self.f_hz = 0.0
            self.run_t = 0.0
            self.q_del += (0.0 - self.q_del) / self.tau_hx * dt
            self.aux_on = False
            return {"q_sens": 0.0, "latent_w": 0.0, "reevap_w": 0.0,
                    "power": 0.0, "aux_w": 0.0}
        # 化霜判定：湿冷工况（室外低温且露点贴近干球 → 结霜快）累计运转
        dew_out = self._dew(w_out) if w_out > 1e-5 else -30.0
        humid_cold = (t_out < 5.0) and (t_out - dew_out < 6.0)
        if self.defrost_left > 0.0:
            self.defrost_left -= dt
            power = 0.5 * self.q_rated_heat / 3.0
            return {"q_sens": 0.0, "latent_w": 0.0, "reevap_w": 0.0,
                    "power": power, "aux_w": self._aux(dt, t_room, t_set, True)}
        if self.f_hz <= 0.0:
            self.f_hz = self.f_start
            self.run_t = 0.0
        self.run_t += dt
        if humid_cold:
            # -7~5℃ 湿冷区结霜最快；更低温空气干燥，结霜减慢
            rate = 1.0 if t_out > -7.0 else 0.4
            self.frost_accum += rate * dt
            if self.frost_accum >= self.defrost_run_interval_s:
                self.frost_accum = 0.0
                self.defrost_left = self.defrost_duration_s
        cap_ft = max(0.35, min(1.15, 1.0 + self.kq_oat_h * (t_out - 8.3)))
        q_ss = self.q_rated_heat * (self.f_hz / self.f_rated) ** 0.95 * cap_ft * self.cap_ff
        self.tc = t_room + self.dt_coil_h_off \
            + self.dt_coil_h_k * (q_ss / self.q_rated_heat) / self.flow_factor
        if self.variable:
            if self.run_t >= self.start_hold_s:
                df = self.ki * (tcs - self.tc) * dt
                df = max(-self.ramp_hz_s * dt, min(self.ramp_hz_s * dt, df))
                self.f_hz = max(self.f_min, min(self.f_max, self.f_hz + df))
        else:
            self.f_hz = min(self.f_rated, self.f_hz + self.ramp_hz_s * 2.0 * dt)
        self.q_del += (q_ss - self.q_del) / self.tau_hx * dt
        cop = self.cop_rated_heat + self.ke_oat_h * (t_out - 8.3) - 0.05 * (self.tc - 45.0)
        cop = max(1.3, min(5.5, cop))
        power = q_ss / cop + self.fan_power()
        aux = self._aux(dt, t_room, t_set, False)
        return {"q_sens": -(self.q_del + aux), "latent_w": 0.0, "reevap_w": 0.0,
                "power": power, "aux_w": aux}

    def _aux(self, dt, t_room, t_set, defrosting):
        if not self.aux_enabled:
            return 0.0
        if defrosting:
            return self.aux_heat_w        # 化霜期辅热顶上（防冷风）
        if self.aux_on:
            if t_room >= t_set - 0.5:
                self.aux_on = False
        else:
            if t_room <= t_set - 1.5:
                self.aux_on = True
        return self.aux_heat_w if self.aux_on else 0.0

    @staticmethod
    def _dew(w: float) -> float:
        from .climate import dew_from_w
        return dew_from_w(w)

    def measured_capacity(self) -> float:
        """算法层可见的冷媒侧能力（总能力 + 系统偏差）。化霜期 ≈0。"""
        if self.defrost_left > 0.0:
            return 0.0
        return self.q_del * (1.0 + self.meas_bias)


@dataclass
class Thermostat:
    """北美数字温控器：滞环 + 最小开停机；制冷/制热双模式。算法层不可见。"""

    t_set: float = 24.5
    deadband: float = 0.56
    sample_period: float = 60.0
    min_on: float = 180.0
    min_off: float = 300.0

    call: bool = False
    _last_sample: float = field(default=-1e9, repr=False)
    _last_switch: float = field(default=-1e9, repr=False)

    def step(self, t: float, t_room: float, mode: str = "cooling") -> bool:
        if t - self._last_sample < self.sample_period:
            return self.call
        self._last_sample = t
        hi = self.t_set + self.deadband / 2.0
        lo = self.t_set - self.deadband / 2.0
        if mode == "cooling":
            if self.call:
                if t_room <= lo and t - self._last_switch >= self.min_on:
                    self.call = False
                    self._last_switch = t
            elif t_room >= hi and t - self._last_switch >= self.min_off:
                self.call = True
                self._last_switch = t
        else:
            if self.call:
                if t_room >= hi and t - self._last_switch >= self.min_on:
                    self.call = False
                    self._last_switch = t
            elif t_room <= lo and t - self._last_switch >= self.min_off:
                self.call = True
                self._last_switch = t
        return self.call
