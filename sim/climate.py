"""美国典型区域气候数据（ANSI/ASHRAE Standard 169 / ASHRAE Fundamentals 设计工况）。

数据说明
--------
下表数值为 ASHRAE Fundamentals（2017/2021 版）公开设计工况的内置近似值：
- summer_db04：制冷设计干球温度（0.4% 年超越概率），℃
- summer_range：夏季平均日较差（mean daily range），℃
- summer_wb：与 0.4% 干球重合的平均湿球温度（MCWB），℃
- summer_dew：夏季典型露点（用于渗透湿负荷；露点日内波动远小于干球，按日恒定处理），℃
- winter_db996：供暖设计干球温度（99.6%），℃

本开发环境无法访问 ashrae-meteo.info / ashrae.org（出口策略限制），发布前请
对照官网或 ashrae-meteo.info 复核一遍（每站 5 个数，几分钟即可）。曲线生成
方法本身（峰值 + 日较差余弦轮廓）即 ASHRAE 设计日方法的标准简化。
"""

from __future__ import annotations

from dataclasses import dataclass

DAY_S = 86400.0


@dataclass(frozen=True)
class Location:
    key: str
    name: str
    zone: str            # ASHRAE 169 气候区
    summer_db04: float   # ℃
    summer_range: float  # ℃
    summer_wb: float     # ℃（MCWB）
    summer_dew: float    # ℃
    winter_db996: float  # ℃
    winter_range: float  # ℃
    solar_peak_w: float  # 夏季晴天透过得热峰值建议值 W（150~200m² 住宅量级）
    humid: bool          # 湿区（化霜频繁、潜热负荷显著）


LOCATIONS: dict[str, Location] = {loc.key: loc for loc in [
    #        key          名称                 区    db04  rng   wb    dew   w996  wrng  solar humid
    Location("los_angeles", "洛杉矶 CA（沿海）", "3B", 29.4,  7.0, 17.9, 15.0,  7.2, 5.0, 2400, False),
    Location("fresno",      "弗雷斯诺 CA（内陆）","3B", 40.0, 17.0, 21.1, 12.0, -0.6, 8.0, 3000, False),
    Location("houston",     "休斯顿 TX",        "2A", 36.1, 10.0, 25.0, 23.5, -1.1, 7.0, 2600, True),
    Location("dallas",      "达拉斯 TX",        "3A", 38.9, 11.0, 23.9, 21.0, -6.1, 8.0, 2800, True),
    Location("miami",       "迈阿密 FL",        "1A", 33.3,  6.7, 25.6, 24.0,  8.9, 6.0, 2500, True),
    Location("orlando",     "奥兰多 FL",        "2A", 34.4,  9.0, 24.4, 23.5,  3.9, 7.0, 2500, True),
    Location("boston",      "波士顿 MA",        "5A", 32.8,  8.9, 22.8, 19.0,-13.9, 6.0, 2300, True),
    Location("new_york",    "纽约 NY",          "4A", 32.8,  7.5, 23.3, 20.0,-10.6, 6.0, 2300, True),
]}

# 保温档（对应 IECC 各气候区常见水平；UA 为 160m² 独栋含渗透风的综合值 W/K）
INSULATION_LEVELS = {
    "poor":   {"name": "老房（保温差）", "ua": 520, "ach": 1.0},
    "medium": {"name": "普通存量房",     "ua": 350, "ach": 0.6},
    "good":   {"name": "新规范（保温好）","ua": 230, "ach": 0.35},
}

SCENARIOS = ("summer_design", "summer_typical", "winter_design", "winter_typical")


def scenario_params(loc: Location, scenario: str) -> dict:
    """把地点 + 场景转成天气参数（峰值/日较差余弦轮廓 + 恒定露点）。"""
    if scenario == "summer_design":
        peak, rng, dew = loc.summer_db04, loc.summer_range, loc.summer_dew
        solar = loc.solar_peak_w
        mode = "cooling"
    elif scenario == "summer_typical":
        peak, rng, dew = loc.summer_db04 - 4.0, loc.summer_range, loc.summer_dew - 1.0
        solar = loc.solar_peak_w * 0.85
        mode = "cooling"
    elif scenario == "winter_design":
        low, rng = loc.winter_db996, loc.winter_range
        peak = low + rng
        dew = min(low - 2.0, 0.0)
        solar = loc.solar_peak_w * 0.45
        mode = "heating"
    elif scenario == "winter_typical":
        low, rng = loc.winter_db996 + 6.0, loc.winter_range
        peak = low + rng
        dew = min(low - 2.0, 2.0)
        solar = loc.solar_peak_w * 0.5
        mode = "heating"
    else:
        raise ValueError(scenario)
    return {
        "t_mean": peak - rng / 2.0,
        "t_amp": rng / 2.0,
        "dew_point": dew,
        "solar_peak_w": solar,
        "mode": mode,
    }


# ---- 湿空气性质（Magnus 公式，总压 101325 Pa） ----

def p_sat(t_c: float) -> float:
    """饱和水蒸气压 Pa。"""
    import math
    return 610.94 * math.exp(17.625 * t_c / (t_c + 243.04))


def w_from_dew(dew_c: float) -> float:
    """由露点求含湿量 kg/kg。"""
    pv = p_sat(dew_c)
    return 0.622 * pv / (101325.0 - pv)


def rh_from_w(t_c: float, w: float) -> float:
    """由干球与含湿量求相对湿度 0..1。"""
    pv = w * 101325.0 / (0.622 + w)
    return max(0.0, min(1.0, pv / p_sat(t_c)))


def dew_from_w(w: float) -> float:
    import math
    pv = max(1.0, w * 101325.0 / (0.622 + w))
    x = math.log(pv / 610.94)
    return 243.04 * x / (17.625 - x)
