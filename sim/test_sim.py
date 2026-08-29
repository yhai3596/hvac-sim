"""核心逻辑单元测试。用法：python3 -m sim.test_sim"""

from __future__ import annotations

from .algorithms import (BinStore, V144Learner, temp_bin, is_day,
                         nominal_target)
from .climate import LOCATIONS, w_from_dew, rh_from_w, scenario_params
from .models import Thermostat, Device, House, w_sat
from .scenarios import run_scenario, run_setpoint_step, design_load


def test_temp_bin():
    # PPT 幻灯 3：13~16℃ 为区间 5；幻灯 6：9℃ 在区间 3、14℃ 在区间 5
    assert temp_bin(-3) == 1
    assert temp_bin(9) == 3
    assert temp_bin(13.0) == 5 and temp_bin(15.9) == 5
    assert temp_bin(35) == 12
    assert temp_bin(50) == 16
    # 制热分箱：幻灯 8：-6℃~0℃ 对应区间 6~8
    assert temp_bin(-6, "heating") == 6
    assert temp_bin(-0.5, "heating") == 8
    assert temp_bin(-25, "heating") == 1
    assert temp_bin(25, "heating") == 16


def test_day_night():
    assert is_day(7 * 3600) and is_day(18.99 * 3600)
    assert not is_day(19 * 3600) and not is_day(3 * 3600)


def test_binstore_trust():
    st = BinStore()
    for q in (100, 102, 98, 101, 99, 103, 97):
        st.add(q)
    assert st.coeff is not None and 97 <= st.coeff <= 103
    c0 = st.coeff
    st.add(1000)                     # 区间外 → 丢弃
    assert st.coeff == c0


def test_psychrometrics():
    assert 0.013 < w_from_dew(19.0) < 0.0145
    assert abs(rh_from_w(25.0, w_sat(25.0)) - 1.0) < 0.01
    assert rh_from_w(25.0, 0.010) < rh_from_w(20.0, 0.010)   # 同含湿量，越热 RH 越低


def test_climate_scenarios():
    for loc in LOCATIONS.values():
        sp = scenario_params(loc, "summer_design")
        assert sp["mode"] == "cooling"
        assert sp["t_mean"] + sp["t_amp"] == loc.summer_db04   # 峰值=设计干球
        wp = scenario_params(loc, "winter_design")
        assert wp["mode"] == "heating"
        assert abs((wp["t_mean"] - wp["t_amp"]) - loc.winter_db996) < 0.01


def test_thermostat_both_modes():
    th = Thermostat(t_set=24.0, deadband=1.0, sample_period=0, min_on=0, min_off=0)
    assert th.step(0, 24.6, "cooling") is True
    assert th.step(1, 23.4, "cooling") is False
    th2 = Thermostat(t_set=21.0, deadband=1.0, sample_period=0, min_on=0, min_off=0)
    assert th2.step(0, 20.4, "heating") is True
    assert th2.step(1, 21.6, "heating") is False


def test_device_cooling_humid_shr():
    d = Device()
    w_humid = w_from_dew(22.0)
    for _ in range(120):   # 20 分钟
        d.step(10, "cooling", True, 33.0, w_humid, 7.0, 25.0, w_humid, 24.5)
    assert d.shr < 0.9            # 湿工况有潜热
    assert d.f_hz > d.f_start     # 已从启动频率爬升
    d2 = Device()
    w_dry = w_from_dew(5.0)
    for _ in range(120):
        d2.step(10, "cooling", True, 38.0, w_dry, 7.0, 25.0, w_dry, 24.5)
    assert d2.shr > 0.98          # 干工况几乎全显热


def test_device_airflow_direction():
    # 定频机：Te 浮动，风量低 → 盘管更冷；中等湿度下 SHR 更低（除湿强）
    lo = Device(cfm_per_ton=330, variable=False)
    hi = Device(cfm_per_ton=450, variable=False)
    w_mid = w_from_dew(17.0)
    w = w_from_dew(22.0)
    for _ in range(120):
        lo.step(10, "cooling", True, 33.0, w_mid, 7.0, 25.0, w_mid, 24.5)
        hi.step(10, "cooling", True, 33.0, w_mid, 7.0, 25.0, w_mid, 24.5)
    assert lo.te < hi.te
    assert lo.shr < hi.shr
    # 变频机：PI 把 Te 钉在 Tes → SHR 由 Tes 决定；高风量同 Te 下需更高频率
    lov = Device(cfm_per_ton=330)
    hiv = Device(cfm_per_ton=450)
    for _ in range(240):
        lov.step(10, "cooling", True, 33.0, w, 7.0, 25.0, w, 24.5)
        hiv.step(10, "cooling", True, 33.0, w, 7.0, 25.0, w, 24.5)
    assert abs(lov.te - 7.0) < 0.5 and abs(hiv.te - 7.0) < 0.5
    assert hiv.f_hz > lov.f_hz
    # Tes 抬高 → 盘管变暖 → SHR 上升（除湿变弱）——V1.44 节能-除湿矛盾的机理
    base, warm = Device(), Device()
    for _ in range(240):
        base.step(10, "cooling", True, 33.0, w_mid, 7.0, 25.0, w_mid, 24.5)
        warm.step(10, "cooling", True, 33.0, w_mid, 12.0, 25.0, w_mid, 24.5)
    assert warm.shr > base.shr + 0.05


def test_device_heating_and_defrost():
    d = Device(defrost_run_interval_s=600)
    w = w_from_dew(-2.0)          # 湿冷 → 结霜
    saw_defrost = False
    for _ in range(240):          # 40 分钟
        out = d.step(10, "heating", True, -1.0, w, 45.0, 20.0, 0.004, 21.1)
        if d.defrost_left > 0:
            saw_defrost = True
            assert d.measured_capacity() == 0.0   # PPT：化霜能力 0kW
    assert saw_defrost
    assert out["q_sens"] <= 0.0   # 制热为注入热量


def test_house_moisture():
    h = House(t_room=25, t_mass=25, w_room=0.010)
    w_out = w_from_dew(24.0)
    for _ in range(720):          # 2 小时无除湿 → 渗透+内扰增湿
        h.step(10, 32, w_out, 0, 0, 0, 0)
    assert h.w_room > 0.010
    h2 = House(t_room=25, t_mass=25, w_room=0.012)
    for _ in range(720):          # 除湿功率大于渗透+内扰湿负荷 → 降湿
        h2.step(10, 32, w_out, 0, 0, 3000, 0)
    assert h2.w_room < 0.012


def test_v144_heating_direction():
    algo = V144Learner(mode="heating", prelearn_q=4000)
    assert algo._tgt == nominal_target("heating")
    algo._sig_up = True
    t0 = algo._tgt
    algo._cycle_tgt0 = t0
    algo._started = True
    algo._call_prev = True
    algo._t_on_start = 0.0
    algo.update(100, 10, True, -5.0, 4000)
    assert algo._tgt > t0         # 制热能力 UP → Tcs 上调


def test_end_to_end_cooling():
    r = run_scenario("C", "miami", days=2.0, prelearn_q=design_load("miami")["total_w"])
    assert r.energy_kwh > 0 and len(r.cycles) > 3
    assert 0.3 < r.rh_mean < 0.9
    assert r.dehum_kg > 0
    done = [c for c in r.cycles if c.hz95_s is not None]
    assert done                  # 压缩机到峰值时间可测


def test_end_to_end_heating():
    # 典型冬日可保温；设计极端日热泵+辅热不足属真实现象（见验证报告 V5）
    r = run_scenario("B", "boston", scenario="winter_typical", days=1.0)
    assert r.energy_kwh > 0 and r.cool_kwh > 0
    assert r.rmse_c < 2.5


def test_setpoint_step():
    r = run_setpoint_step("B", "fresno")
    assert r.pulldown_s is not None and r.pulldown_s > 0


def test_fan_modes():
    from .climate import w_from_dew as wd
    w = wd(20.0)
    # 两档风：1 档 70%
    d = Device(fan_ctrl="two", two_stage=1)
    d.step(10, "cooling", True, 33.0, w, 7.0, 25.0, w, 24.5)
    assert abs(d.fan_pct - 0.70) < 1e-9
    # 自动风：60% 起，50min 内保持，之后每 10min +10% 直到 100%
    d2 = Device(fan_ctrl="auto")
    for _ in range(6 * 49):      # 49 min
        d2.step(10, "cooling", True, 33.0, w, 7.0, 25.0, w, 24.5)
    assert abs(d2.fan_pct - 0.60) < 1e-9
    for _ in range(6 * 2):       # 51 min：第一次提档
        d2.step(10, "cooling", True, 33.0, w, 7.0, 25.0, w, 24.5)
    assert abs(d2.fan_pct - 0.70) < 1e-9
    for _ in range(6 * 45):      # 96 min：应到 100% 封顶
        d2.step(10, "cooling", True, 33.0, w, 7.0, 25.0, w, 24.5)
    assert abs(d2.fan_pct - 1.00) < 1e-9
    # 停机重启 → 回到初始风量
    for _ in range(6 * 10):
        d2.step(10, "cooling", False, 33.0, w, 7.0, 25.0, w, 24.5)
    d2.step(10, "cooling", True, 33.0, w, 7.0, 25.0, w, 24.5)
    assert abs(d2.fan_pct - 0.60) < 1e-9
    # 自动风参数可调
    d3 = Device(fan_ctrl="auto", auto_init=0.5, auto_wait_s=600, auto_step_s=300,
                auto_step=0.25)
    for _ in range(6 * 16):      # 16 min：600s 保持 + 2 次 +25%
        d3.step(10, "cooling", True, 33.0, w, 7.0, 25.0, w, 24.5)
    assert abs(d3.fan_pct - 1.00) < 1e-9


def test_satisfaction_anchor():
    from .scenarios import make_stack
    for sat in (0.7, 1.0, 1.3):
        _, house, device, th, _ = make_stack("B", "miami", tons=3.0,
                                             satisfaction=sat)
        # 反算：缩放后设计负荷 × 满足度 = 额定能力
        from .scenarios import design_load
        base = design_load("miami")
        scale = house.ua / 350.0
        assert abs(base["total_w"] * scale * sat - 3 * 3517.0) / (3 * 3517.0) < 0.02
    # 5 Ton 选择生效
    _, _, dev5, _, _ = make_stack("B", "miami", tons=5.0, satisfaction=1.0)
    assert abs(dev5.q_rated - 5 * 3517.0) < 1e-6


def main():
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == "__main__":
    main()
