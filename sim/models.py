"""物理子模型：天气、房间热模型（2R2C）、变频 DX 设备、温控器。

单位约定：温度 ℃，功率 W，能量 J，时间 s。
模型依据见 docs/simulation-plan.md 第 2 节。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

DAY_S = 24 * 3600.0


@dataclass
class Weather:
    """室外温度：日周期正弦；日照得热：白天正弦包络。"""

    t_mean: float = 30.0       # 日平均室外温度 ℃
    t_amp: float = 5.0         # 日振幅 ℃（0 = 恒温工况）
    t_peak_hour: float = 15.0  # 最高温出现时刻
    solar_peak_w: float = 2500.0  # 日照得热峰值 W
    sunrise_hour: float = 6.5
    sunset_hour: float = 19.5

    def outdoor_temp(self, t: float) -> float:
        phase = 2.0 * math.pi * (t / DAY_S - self.t_peak_hour / 24.0)
        return self.t_mean + self.t_amp * math.cos(phase)

    def solar_gain(self, t: float) -> float:
        hour = (t % DAY_S) / 3600.0
        if hour <= self.sunrise_hour or hour >= self.sunset_hour:
            return 0.0
        frac = (hour - self.sunrise_hour) / (self.sunset_hour - self.sunrise_hour)
        return self.solar_peak_w * math.sin(math.pi * frac)


@dataclass
class House:
    """2R2C 集总参数房间热模型（ISO 13790 风格简化）。"""

    ua: float = 350.0            # 围护结构综合传热系数 W/K（含渗透风）
    c_air: float = 3.0e6         # 空气+轻质内饰热容 J/K
    c_mass: float = 40.0e6       # 蓄热体热容 J/K
    h_mass: float = 500.0        # 空气-蓄热体换热系数 W/K
    f_air: float = 0.4           # 日照/内扰进入空气节点的份额
    q_internal: float = 300.0    # 内部得热 W
    t_room: float = 26.5         # 初始室温 ℃
    t_mass: float = 26.5         # 初始蓄热体温度 ℃

    def step(self, dt: float, t_out: float, q_solar: float, q_ac_sens: float) -> None:
        """推进一个时间步。q_ac_sens：空调显热制冷量 W（正值为移出热量）。"""
        gains = q_solar + self.q_internal
        dq_air = (
            self.ua * (t_out - self.t_room)
            + self.h_mass * (self.t_mass - self.t_room)
            + self.f_air * gains
            - q_ac_sens
        )
        dq_mass = self.h_mass * (self.t_room - self.t_mass) + (1.0 - self.f_air) * gains
        self.t_room += dq_air / self.c_air * dt
        self.t_mass += dq_mass / self.c_mass * dt


@dataclass
class Device:
    """变频 DX 外机的一阶线性化性能模型。

    能力由目标蒸发温度 Tes 与室外温度决定（Tes 越低 → 压缩机频率越高 → 能力越大、
    COP 越低）。定频基线机型将 variable=False：Tes 固定为 tes_nom 且 COP 用 cop_fixed。
    """

    q_rated: float = 7000.0      # 额定制冷能力 W @ (35℃, Tes_nom)
    cop_rated: float = 3.3       # 额定 COP（变频）
    cop_fixed: float = 3.0       # 定频基线 COP
    tes_nom: float = 7.0         # 标称目标蒸发温度 ℃
    kq_oat: float = 0.010        # 室外温度每降 1℃ 能力增益
    ke_oat: float = 0.020        # 室外温度每降 1℃ COP 增益
    kq_te: float = -0.040        # Tes 每升 1℃ 能力变化
    ke_te: float = 0.028         # Tes 每升 1℃ COP 变化
    tau_start: float = 60.0      # 启动能力一阶时间常数 s
    shr: float = 0.75            # 显热比
    meas_bias: float = 0.0       # 冷媒侧能力测量系统偏差（模拟 Gr×Δh 误差）
    variable: bool = True

    _q_delivered: float = field(default=0.0, repr=False)  # 含启动惯性的当前总能力 W

    def steady_capacity(self, t_out: float, tes: float) -> float:
        f_oat = 1.0 + self.kq_oat * (35.0 - t_out)
        f_te = 1.0 + self.kq_te * (tes - self.tes_nom) if self.variable else 1.0
        return max(0.0, self.q_rated * f_oat * f_te)

    def cop(self, t_out: float, tes: float) -> float:
        base = self.cop_rated if self.variable else self.cop_fixed
        f_oat = 1.0 + self.ke_oat * (35.0 - t_out)
        f_te = 1.0 + self.ke_te * (tes - self.tes_nom) if self.variable else 1.0
        return max(0.5, base * f_oat * f_te)

    def step(self, dt: float, running: bool, t_out: float, tes: float) -> tuple[float, float]:
        """返回 (显热制冷量 W, 输入功率 W)。

        启动段能力按一阶惯性爬升，而输入功率按稳态取值——短循环因此产生
        启动损失（能力未满而功率已满），复现 on/off 循环退化。
        """
        q_ss = self.steady_capacity(t_out, tes) if running else 0.0
        self._q_delivered += (q_ss - self._q_delivered) / self.tau_start * dt
        if not running and self._q_delivered < 1.0:
            self._q_delivered = 0.0
        power = q_ss / self.cop(t_out, tes) if running else 0.0
        return self._q_delivered * self.shr, power

    def measured_capacity(self) -> float:
        """算法层可见的冷媒侧能力测量值（总能力，含系统偏差）。"""
        return self._q_delivered * (1.0 + self.meas_bias)


@dataclass
class Thermostat:
    """北美典型数字温控器：滞环 + 最小开/停机时间。对算法层完全黑盒。"""

    t_set: float = 24.5          # 设定温度 ℃
    deadband: float = 0.56       # 回差 ℃（默认 1.0°F）
    sample_period: float = 60.0  # 采样周期 s
    min_on: float = 180.0        # 最小开机 s
    min_off: float = 300.0       # 最小停机 s

    call: bool = False
    _last_sample: float = field(default=-1e9, repr=False)
    _last_switch: float = field(default=-1e9, repr=False)

    def step(self, t: float, t_room: float) -> bool:
        if t - self._last_sample < self.sample_period:
            return self.call
        self._last_sample = t
        hi = self.t_set + self.deadband / 2.0
        lo = self.t_set - self.deadband / 2.0
        if self.call:
            if t_room <= lo and t - self._last_switch >= self.min_on:
                self.call = False
                self._last_switch = t
        else:
            if t_room >= hi and t - self._last_switch >= self.min_off:
                self.call = True
                self._last_switch = t
        return self.call
