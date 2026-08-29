"""控制算法层。算法只能看到外机可见信息：

- thermo-on/off 时序（温控器 Y 信号）
- 室外温度
- 冷媒侧测得的输出能力（含可配置偏差）
- 时钟（昼/夜判断）

室温、设定温度、回差对算法不可见——忠实还原内外机无通讯约束。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# 制冷负荷分箱边界（PPT V1.44 幻灯 1；左闭右开，区间1 为 <0℃，区间16 为 ≥46℃）
COOLING_BIN_EDGES = [0, 5, 10, 13, 16, 19, 22, 25, 28, 31, 34, 37, 40, 43, 46]

DAY_START_H = 7.0    # 白天 [7:00, 19:00)
DAY_END_H = 19.0


def temp_bin(t_out: float) -> int:
    """室外温度 → 区间号 1..16。"""
    for i, edge in enumerate(COOLING_BIN_EDGES):
        if t_out < edge:
            return i + 1
    return len(COOLING_BIN_EDGES) + 1


def is_day(t: float) -> bool:
    hour = (t % 86400.0) / 3600.0
    return DAY_START_H <= hour < DAY_END_H


class Algorithm:
    """基类。simulator 每个时间步调用 update()，返回当前 Tes 目标。"""

    name = "base"

    def update(self, t: float, dt: float, call: bool, t_out: float,
               q_meas: float) -> float:
        raise NotImplementedError


@dataclass
class FixedSpeedBaseline(Algorithm):
    """算法 A：定频 on/off 基线（被替代对象）。Tes 恒为标称值，设备侧 variable=False。"""

    tes_nom: float = 7.0
    name: str = "A-定频基线"

    def update(self, t, dt, call, t_out, q_meas):
        return self.tes_nom


@dataclass
class FixedTes(Algorithm):
    """算法 B：变频、固定 Tes（不学习）。"""

    tes: float = 7.0
    name: str = "B-固定Tes"

    def update(self, t, dt, call, t_out, q_meas):
        return self.tes


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
        # 中心值：与其余样本差分绝对值之和最小者
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
        # 已有系数：仅接纳信赖区间内的新样本，替换距中心最远的旧样本
        if self.trust_lo <= q <= self.trust_hi:
            far = max(self.samples, key=lambda x: abs(x - self.coeff))
            self.samples.remove(far)
            self.samples.append(q)
            self._recompute()
        # 区间外 → 丢弃


@dataclass
class V144Learner(Algorithm):
    """算法 C：PPT V1.44 复刻——负荷预测 + Tes 自学习。

    机制：循环定义与有效性判据、外温分箱×昼/夜负荷系数学习、目标总负荷
    （STEP1~3）、运转中每 20s 能力恒定外推预测（STEP4）、120/110/90/80
    滞环产生能力 UP/DOWN 信号、Tes 分箱保存与步进调整。
    """

    ta_min: float = 60.0          # 目标运转时间 TA（分钟）
    dead_hi: float = 1.20         # DOWN 置位阈值
    dead_hi_off: float = 1.10     # DOWN 复位阈值
    dead_lo: float = 0.80         # UP 置位阈值
    dead_lo_off: float = 0.90     # UP 复位阈值
    tes_nom: float = 7.0
    tes_min: float = 4.0
    tes_max: float = 14.0
    tes_rate: float = 0.10        # Tes 调整速率 ℃/min（信号 ON 期间）
    tes_cycle_max: float = 1.0    # 每循环最大 Tes 调整量 ℃
    eval_period: float = 20.0     # STEP4 评估周期 s
    sample_delay: float = 180.0   # 压缩机启动后 3min 开始能力采样
    t1_min: float = 5 * 60.0      # 有效循环判据
    t1_max: float = 120 * 60.0
    t2_min: float = 5 * 60.0
    t2_max: float = 240 * 60.0
    prelearn_q: float | None = None   # 预学习：直接给定所有箱的 q (W)，跳过冷启动

    name: str = "C-V1.44"

    # ---- 内部状态 ----
    _bins: dict = field(default_factory=dict)       # (bin, 'day'/'night') -> BinStore
    _tes_store: dict = field(default_factory=dict)  # bin -> 学得的 Tes
    _tes_active: float = field(default=7.0)
    _call_prev: bool = False
    _t_on_start: float = 0.0
    _t_off_start: float = 0.0
    _cycle_energy: float = 0.0      # 本循环冷媒侧测得能力积分 J
    _off_load_int: float = 0.0      # STEP1: t1 段 q(bin) 积分 J
    _off_load_known: bool = True
    _target_total: float | None = None
    _ta_eff: float = 0.0            # 有效目标时间 s（超时逐次 +10min）
    _next_eval: float = 0.0
    _sig_up: bool = False
    _sig_down: bool = False
    _started: bool = False
    _t1: float = 0.0
    _entry_bin: int = 1
    _cycle_tes0: float = 7.0
    # 对外可观测的调试/可视化状态
    last_ratio: float | None = None
    signals_log: list = field(default_factory=list)

    def __post_init__(self):
        self._tes_active = self.tes_nom
        self._cycle_tes0 = self.tes_nom

    # ---- 负荷系数访问 ----
    def _store(self, b: int, day: bool) -> BinStore:
        key = (b, "day" if day else "night")
        if key not in self._bins:
            st = BinStore()
            if self.prelearn_q is not None:
                # 预学习种子带 ±15% 展布：信赖区间非退化，保留现场自适应能力
                n = st.min_samples
                st.samples = [self.prelearn_q * (1.0 + 0.30 * (i / (n - 1) - 0.5))
                              for i in range(n)]
                st._recompute()
            self._bins[key] = st
        return self._bins[key]

    def q_coeff(self, t_out: float, t: float) -> float | None:
        return self._store(temp_bin(t_out), is_day(t)).coeff

    def tes_for_bin(self, b: int) -> float:
        return self._tes_store.get(b, self.tes_nom)

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
            # STEP1 素材：停机段按当前箱负荷系数积分
            q = self.q_coeff(t_out, t)
            if q is None:
                self._off_load_known = False
            else:
                self._off_load_int += q * dt
            return self._tes_active

        # 运转中：启动 3min 后开始能力采样与 STEP4 评估（采样期内能力爬坡不外推）
        run_t = t - self._t_on_start
        if run_t >= self.sample_delay:
            self._cycle_energy += q_meas * dt
            if self._target_total is not None and t >= self._next_eval:
                self._next_eval = t + self.eval_period
                self._evaluate(t, run_t, q_meas)
        # 信号 ON 期间按速率调整 Tes（受全局范围与每循环限幅约束）
        if self._sig_up:
            self._tes_active -= self.tes_rate * dt / 60.0
        elif self._sig_down:
            self._tes_active += self.tes_rate * dt / 60.0
        lo = max(self.tes_min, self._cycle_tes0 - self.tes_cycle_max)
        hi = min(self.tes_max, self._cycle_tes0 + self.tes_cycle_max)
        self._tes_active = min(hi, max(lo, self._tes_active))
        return self._tes_active

    # ---- 事件处理 ----
    def _thermo_on(self, t: float, t_out: float) -> None:
        b = temp_bin(t_out)
        self._entry_bin = b
        self._tes_active = self.tes_for_bin(b)
        self._cycle_tes0 = self._tes_active
        self._t_on_start = t
        self._t1 = t - self._t_off_start
        self._cycle_energy = 0.0
        self._next_eval = t + self.eval_period
        self._sig_up = self._sig_down = False
        self._ta_eff = self.ta_min * 60.0
        # STEP2/3：目标总负荷（需要 t1 段与当前箱负荷系数都已学成）
        q_now = self.q_coeff(t_out, t)
        if q_now is not None and self._off_load_known:
            t1_load = self._off_load_int
            if self._t1 > self.t1_max:
                # t1 超过 120min：只取最近 120min 的负荷（按比例近似）
                t1_load *= self.t1_max / max(self._t1, 1.0)
            self._target_total = t1_load + q_now * self.ta_min * 60.0
        else:
            self._target_total = None

    def _thermo_off(self, t: float, t_out: float) -> None:
        t2 = t - self._t_on_start
        # 学得的 Tes 存回进入循环时的温度箱
        if self._target_total is not None:
            self._tes_store[self._entry_bin] = self._tes_active
        # 负荷采样：有效性判据
        t0 = self._t1 + t2
        valid = (self.t1_min <= self._t1 < self.t1_max
                 and self.t2_min <= t2 < self.t2_max and t0 > 0)
        if valid:
            q_avg = self._cycle_energy / t0   # 循环平均负荷 W
            # 昼/夜归属：t1≥t2 按 t1 起点时刻，否则按 t2 结束时刻
            attr_t = self._t_off_start if self._t1 >= t2 else t
            self._store(temp_bin(t_out), is_day(attr_t)).add(q_avg)
        # 复位停机段积分
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
            self._ta_eff += 10 * 60.0     # 超过目标时间：TA+10min 逐次延长
        predicted = self._cycle_energy + q_meas * (self._ta_eff - run_t)
        ratio = predicted / max(self._target_total, 1.0)
        self.last_ratio = ratio
        if ratio >= self.dead_hi:
            if not self._sig_down:
                self.signals_log.append((t, "DOWN"))
            self._sig_down, self._sig_up = True, False
        elif ratio <= self.dead_lo:
            if not self._sig_up:
                self.signals_log.append((t, "UP"))
            self._sig_up, self._sig_down = True, False
        else:
            if self._sig_down and ratio < self.dead_hi_off:
                self._sig_down = False
            if self._sig_up and ratio > self.dead_lo_off:
                self._sig_up = False
