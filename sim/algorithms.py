"""控制算法层（制冷 + 制热）。算法只能看到外机可见信息：

- thermo-on/off 时序（温控器 Y/W 信号）
- 室外温度
- 冷媒侧测得的输出能力（含可配置偏差；化霜期 ≈0，与 PPT"化霜能力 0kW"一致）
- 时钟（昼/夜判断）

室温、设定温度、回差对算法不可见——忠实还原内外机无通讯约束。

控制变量：制冷为目标蒸发温度 Tes（能力 UP=Tes↓），制热为目标冷凝温度 Tcs
（能力 UP=Tcs↑）。V1.44 机制：温度分箱×昼/夜负荷系数学习（7 天采样中心值法
+ 70% 信赖区间）、目标总负荷（STEP1~3）、每 20s 能力恒定外推预测（STEP4）、
120/110/90/80 滞环 UP/DOWN 信号、目标值分箱保存。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# 温度分箱边界（PPT V1.44；左闭右开，区间1 为下开区间，区间16 为上开区间）
COOLING_BIN_EDGES = [0, 5, 10, 13, 16, 19, 22, 25, 28, 31, 34, 37, 40, 43, 46]
HEATING_BIN_EDGES = [-20, -17, -14, -11, -8, -5, -2, 1, 4, 7, 10, 13, 16, 19, 22]

DAY_START_H = 7.0    # 白天 [7:00, 19:00)
DAY_END_H = 19.0

TES_NOM, TES_MIN, TES_MAX = 7.0, 4.0, 14.0
TCS_NOM, TCS_MIN, TCS_MAX = 45.0, 38.0, 52.0


def temp_bin(t_out: float, mode: str = "cooling") -> int:
    edges = COOLING_BIN_EDGES if mode == "cooling" else HEATING_BIN_EDGES
    for i, edge in enumerate(edges):
        if t_out < edge:
            return i + 1
    return len(edges) + 1


def bin_range(b: int, mode: str = "cooling") -> str:
    edges = COOLING_BIN_EDGES if mode == "cooling" else HEATING_BIN_EDGES
    if b == 1:
        return f"<{edges[0]}"
    if b == len(edges) + 1:
        return f"≥{edges[-1]}"
    return f"{edges[b - 2]}~{edges[b - 1]}"


def is_day(t: float) -> bool:
    hour = (t % 86400.0) / 3600.0
    return DAY_START_H <= hour < DAY_END_H


def nominal_target(mode: str) -> float:
    return TES_NOM if mode == "cooling" else TCS_NOM


class Algorithm:
    """基类。simulator 每个时间步调用 update()，返回当前控制目标（Tes/Tcs）。"""

    name = "base"
    mode = "cooling"

    def update(self, t: float, dt: float, call: bool, t_out: float,
               q_meas: float) -> float:
        raise NotImplementedError


@dataclass
class FixedSpeedBaseline(Algorithm):
    """算法 A：定频 on/off 基线（被替代对象）。设备侧 variable=False。"""

    mode: str = "cooling"
    name: str = "A-定频基线"

    def update(self, t, dt, call, t_out, q_meas):
        return nominal_target(self.mode)


@dataclass
class FixedTarget(Algorithm):
    """算法 B：变频、固定 Tes/Tcs（不学习）。"""

    mode: str = "cooling"
    target: float | None = None
    name: str = "B-固定目标"

    def update(self, t, dt, call, t_out, q_meas):
        return self.target if self.target is not None else nominal_target(self.mode)


@dataclass
class BinStore:
    """单个 (区间, 昼/夜) 的负荷系数样本库——7 天采样法 + 70% 信赖区间。"""

    min_samples: int = 7
    max_samples: int = 21
    trust_ratio: float = 0.70
    trust_expand: float = 1.20
    samples: list = field(default_factory=list)
    coeff: float | None = None       # 负荷系数 q (W)
    trust_lo: float = 0.0
    trust_hi: float = 0.0

    def _recompute(self) -> None:
        data = self.samples
        if len(data) < self.min_samples:
            self.coeff = None
            return
        center = min(data, key=lambda a: sum(abs(a - b) for b in data))
        keep_n = max(1, math.floor(len(data) * self.trust_ratio))
        kept = sorted(data, key=lambda x: abs(x - center))[:keep_n]
        c = sum(kept) / len(kept)
        self.coeff = c
        self.trust_hi = (max(kept) - c) * self.trust_expand + c
        self.trust_lo = (min(kept) - c) * self.trust_expand + c

    def add(self, q: float) -> None:
        if self.coeff is None:
            self.samples.append(q)
            if len(self.samples) > self.max_samples:
                self.samples.pop(0)
            self._recompute()
            return
        if self.trust_lo <= q <= self.trust_hi:
            far = max(self.samples, key=lambda x: abs(x - self.coeff))
            self.samples.remove(far)
            self.samples.append(q)
            self._recompute()
        # 区间外 → 丢弃


@dataclass
class V144Learner(Algorithm):
    """算法 C：PPT V1.44 复刻——负荷预测 + Tes/Tcs 自学习。"""

    mode: str = "cooling"
    ta_min: float = 60.0          # 目标运转时间 TA（分钟）
    dz: float = 0.20              # 判定死区（±20%；滞环为死区一半）
    rate: float = 0.10            # 目标值调整速率 ℃/min（信号 ON 期间）
    cycle_max_adj: float = 1.0    # 每循环最大调整量 ℃
    eval_period: float = 20.0
    sample_delay: float = 180.0   # 启动 3min 后开始采样（制热近似同）
    t1_min: float = 5 * 60.0
    t1_max: float = 120 * 60.0
    t2_min: float = 5 * 60.0
    t2_max: float = 240 * 60.0
    prelearn_q: float | None = None
    name: str = "C-V1.44"

    # ---- 内部状态 ----
    _bins: dict = field(default_factory=dict)
    _target_store: dict = field(default_factory=dict)   # bin -> 学得的 Tes/Tcs
    _tgt: float = 7.0
    _call_prev: bool = False
    _t_on_start: float = 0.0
    _t_off_start: float = 0.0
    _cycle_energy: float = 0.0
    _off_load_int: float = 0.0
    _off_load_known: bool = True
    _target_total: float | None = None
    _ta_eff: float = 0.0
    _next_eval: float = 0.0
    _sig_up: bool = False
    _sig_down: bool = False
    _started: bool = False
    _t1: float = 0.0
    _entry_bin: int = 1
    _cycle_tgt0: float = 7.0
    last_ratio: float | None = None
    signals_log: list = field(default_factory=list)

    def __post_init__(self):
        self._tgt = nominal_target(self.mode)
        self._cycle_tgt0 = self._tgt

    @property
    def _lims(self) -> tuple[float, float]:
        return (TES_MIN, TES_MAX) if self.mode == "cooling" else (TCS_MIN, TCS_MAX)

    @property
    def _up_dir(self) -> float:
        """能力 UP 时目标值的调整方向：制冷 Tes↓（−1），制热 Tcs↑（+1）。"""
        return -1.0 if self.mode == "cooling" else 1.0

    # ---- 负荷系数 ----
    def _store(self, b: int, day: bool) -> BinStore:
        key = (b, "day" if day else "night")
        if key not in self._bins:
            st = BinStore()
            if self.prelearn_q is not None:
                n = st.min_samples
                st.samples = [self.prelearn_q * (1.0 + 0.30 * (i / (n - 1) - 0.5))
                              for i in range(n)]
                st._recompute()
            self._bins[key] = st
        return self._bins[key]

    def q_coeff(self, t_out: float, t: float) -> float | None:
        return self._store(temp_bin(t_out, self.mode), is_day(t)).coeff

    def target_for_bin(self, b: int) -> float:
        return self._target_store.get(b, nominal_target(self.mode))

    # ---- 主循环 ----
    def update(self, t, dt, call, t_out, q_meas):
        if not self._started:
            self._started = True
            self._call_prev = call
            if call:
                self._t_on_start = t
            else:
                self._t_off_start = t

        if call and not self._call_prev:
            self._thermo_on(t, t_out)
        elif not call and self._call_prev:
            self._thermo_off(t, t_out)
        self._call_prev = call

        if not call:
            q = self.q_coeff(t_out, t)
            if q is None:
                self._off_load_known = False
            else:
                self._off_load_int += q * dt
            return self._tgt

        run_t = t - self._t_on_start
        if run_t >= self.sample_delay:
            self._cycle_energy += q_meas * dt
            if self._target_total is not None and t >= self._next_eval:
                self._next_eval = t + self.eval_period
                self._evaluate(t, run_t, q_meas)
        if self._sig_up:
            self._tgt += self._up_dir * self.rate * dt / 60.0
        elif self._sig_down:
            self._tgt -= self._up_dir * self.rate * dt / 60.0
        g_lo, g_hi = self._lims
        lo = max(g_lo, self._cycle_tgt0 - self.cycle_max_adj)
        hi = min(g_hi, self._cycle_tgt0 + self.cycle_max_adj)
        self._tgt = min(hi, max(lo, self._tgt))
        return self._tgt

    # ---- 事件处理 ----
    def _thermo_on(self, t: float, t_out: float) -> None:
        b = temp_bin(t_out, self.mode)
        self._entry_bin = b
        self._tgt = self.target_for_bin(b)
        self._cycle_tgt0 = self._tgt
        self._t_on_start = t
        self._t1 = t - self._t_off_start
        self._cycle_energy = 0.0
        self._next_eval = t + self.eval_period
        self._sig_up = self._sig_down = False
        self._ta_eff = self.ta_min * 60.0
        q_now = self.q_coeff(t_out, t)
        if q_now is not None and self._off_load_known:
            t1_load = self._off_load_int
            if self._t1 > self.t1_max:
                t1_load *= self.t1_max / max(self._t1, 1.0)
            self._target_total = t1_load + q_now * self.ta_min * 60.0
        else:
            self._target_total = None

    def _thermo_off(self, t: float, t_out: float) -> None:
        t2 = t - self._t_on_start
        if self._target_total is not None:
            self._target_store[self._entry_bin] = self._tgt
        t0 = self._t1 + t2
        valid = (self.t1_min <= self._t1 < self.t1_max
                 and self.t2_min <= t2 < self.t2_max and t0 > 0)
        if valid:
            q_avg = self._cycle_energy / t0
            attr_t = self._t_off_start if self._t1 >= t2 else t
            self._store(temp_bin(t_out, self.mode), is_day(attr_t)).add(q_avg)
        self._t_off_start = t
        self._off_load_int = 0.0
        self._off_load_known = True
        self._target_total = None
        self._sig_up = self._sig_down = False
        self.last_ratio = None

    # ---- STEP4：预测与 UP/DOWN 信号 ----
    def _evaluate(self, t: float, run_t: float, q_meas: float) -> None:
        if run_t >= self.t2_max:
            self._sig_up = self._sig_down = False
            return
        while run_t >= self._ta_eff:
            self._ta_eff += 10 * 60.0
        predicted = self._cycle_energy + q_meas * (self._ta_eff - run_t)
        ratio = predicted / max(self._target_total, 1.0)
        self.last_ratio = ratio
        hi, lo = 1.0 + self.dz, 1.0 - self.dz
        hi_off, lo_off = 1.0 + self.dz / 2.0, 1.0 - self.dz / 2.0
        if ratio >= hi:
            if not self._sig_down:
                self.signals_log.append((t, "DOWN"))
            self._sig_down, self._sig_up = True, False
        elif ratio <= lo:
            if not self._sig_up:
                self.signals_log.append((t, "UP"))
            self._sig_up, self._sig_down = True, False
        else:
            if self._sig_down and ratio < hi_off:
                self._sig_down = False
            if self._sig_up and ratio > lo_off:
                self._sig_up = False
