"""核心逻辑单元测试。用法：python -m sim.test_sim"""

from __future__ import annotations

from .algorithms import BinStore, V144Learner, temp_bin, is_day
from .models import Thermostat, Device, House, Weather
from .scenarios import run_scenario


def test_temp_bin():
    # PPT 幻灯 3：13~16℃ 为区间 5；幻灯 6：9℃ 在区间 3、14℃ 在区间 5
    assert temp_bin(-3) == 1
    assert temp_bin(2) == 2
    assert temp_bin(9) == 3
    assert temp_bin(13.0) == 5 and temp_bin(15.9) == 5
    assert temp_bin(35) == 12
    assert temp_bin(50) == 16


def test_day_night():
    assert is_day(7 * 3600) and is_day(18.99 * 3600)
    assert not is_day(19 * 3600) and not is_day(3 * 3600)


def test_binstore_trust():
    st = BinStore()
    for q in (100, 102, 98, 101, 99, 103, 97):
        st.add(q)
    assert st.coeff is not None and 97 <= st.coeff <= 103
    lo, hi, c0 = st.trust_lo, st.trust_hi, st.coeff
    st.add(1000)                     # 区间外 → 丢弃
    assert st.coeff == c0
    st.add(c0 + (hi - c0) * 0.5)     # 区间内 → 接纳并重算
    assert st.coeff != c0 or st.samples != [100, 102, 98, 101, 99, 103, 97]


def test_thermostat_hysteresis():
    th = Thermostat(t_set=24.0, deadband=1.0, sample_period=0, min_on=0, min_off=0)
    assert th.step(0, 24.4) is False      # 未到上限
    assert th.step(1, 24.6) is True       # 超上限 → on
    assert th.step(2, 23.8) is True       # 滞环内保持
    assert th.step(3, 23.4) is False      # 低于下限 → off


def test_device_tes_direction():
    d = Device()
    assert d.steady_capacity(35, 5) > d.steady_capacity(35, 9)   # Tes 低 → 能力大
    assert d.cop(35, 9) > d.cop(35, 5)                            # Tes 高 → COP 高
    assert d.steady_capacity(30, 7) > d.steady_capacity(40, 7)    # 外温低 → 能力大


def test_house_cools_and_heats():
    h = House(t_room=25, t_mass=25)
    for _ in range(360):
        h.step(10, 35, 0, 5000)      # 强制冷
    assert h.t_room < 25
    h2 = House(t_room=25, t_mass=25)
    for _ in range(360):
        h2.step(10, 35, 2000, 0)     # 无空调
    assert h2.t_room > 25


def test_v144_learns_and_adjusts():
    r = run_scenario("C", days=3.0, weather=Weather(t_mean=33, t_amp=2),
                     prelearn_q=4500)
    algo = r.algo
    assert isinstance(algo, V144Learner)
    assert len(algo._tes_store) >= 1          # 有 Tes 学习结果
    assert r.energy_kwh > 0 and len(r.cycles) > 5
    # 预学习种子展布：信赖区间非退化，新样本可被接纳
    st = next(iter(algo._bins.values()))
    assert st.trust_hi > st.trust_lo


def main():
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == "__main__":
    main()
