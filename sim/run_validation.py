"""运行全部验证场景并生成 docs/validation-tables.md。

选型基准：3 Ton（10.55 kW），负荷满足度 satisfaction=1.0（额定能力=建筑设计负荷）
为默认锚定；扫描场景在此基础上改变单一变量。

场景矩阵围绕两项客户反馈组织：
- 达温慢：满足度 × 算法行为 × 压缩机爬坡 的贡献分解
- 除湿不足：Tes 高度 × 满足度（短循环） × 风量匹配/风量模式 的贡献分解

用法：python3 -m sim.run_validation
"""

from __future__ import annotations

import json
from pathlib import Path

from .algorithms import V144Learner
from .scenarios import run_scenario, run_setpoint_step, design_load, TON_W
from .climate import LOCATIONS


def calibrate_q(location: str, scenario: str = "summer_design",
                days: float = 2.0, **kw) -> float:
    """用算法 B 运行标定平均冷媒侧总负荷 W（显热+潜热），作预学习初值。"""
    r = run_scenario("B", location, scenario=scenario, days=days, **kw)
    return (r.cool_kwh + r.latent_kwh) / (r.days * 24) * 1000.0


def md_table(rows: list[dict]) -> str:
    if not rows:
        return "(无数据)"
    keys = list(rows[0].keys())
    out = ["| " + " | ".join(keys) + " |",
           "| " + " | ".join("---" for _ in keys) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(k, "")) for k in keys) + " |")
    return "\n".join(out)


def learner_state(algo) -> dict:
    if not isinstance(algo, V144Learner):
        return {}
    bins = {f"{k[0]}-{k[1]}": round(v.coeff, 0)
            for k, v in algo._bins.items() if v.coeff is not None}
    tgt = {b: round(v, 1) for b, v in sorted(algo._target_store.items())}
    return {"learned_bins": bins, "target_store": tgt,
            "signals": len(algo.signals_log)}


def strip(row: dict, *keys) -> dict:
    for k in keys:
        row.pop(k, None)
    return row


def main() -> None:
    report: list[str] = []
    add = report.append

    add("# 验证数据表（由 sim/run_validation.py 生成）\n")
    add("选型基准：3 Ton（10.55 kW）、负荷满足度 1.0（额定能力=建筑设计负荷）。"
        "地点设计工况为内置 ASHRAE 值（见 sim/climate.py 顶部说明，发布前请复核）。\n")

    # ---- V0 地点设计负荷一览（普通保温基准房） ----
    add("## V0 地点设计负荷（普通保温基准房 → 3/5 Ton 自然配比）\n")
    rows = []
    for key, loc in LOCATIONS.items():
        dl = design_load(key)
        rows.append({
            "地点": loc.name, "气候区": loc.zone,
            "夏设计干球 ℃": loc.summer_db04, "湿球 ℃": loc.summer_wb,
            "冬设计干球 ℃": loc.winter_db996,
            "显热 kW": round(dl["sensible_w"] / 1000, 2),
            "潜热 kW": round(dl["latent_w"] / 1000, 2),
            "3Ton 制冷配比": round(3 * TON_W / dl["total_w"], 2),
            "5Ton 制冷配比": round(5 * TON_W / dl["total_w"], 2),
            "3Ton 制热配比": round(3 * TON_W * 1.09 / max(dl["heating_w"], 1), 2),
        })
    add(md_table(rows))
    add("\n注：满足度参数（50~150%）会把负荷源等比缩放到指定配比，"
        "以下场景默认满足度=1.0（即负荷与所选能力段精确匹配）。\n")

    BASE = dict(tons=3.0, satisfaction=1.0)

    # ---- V1 除湿 vs Tes ----
    add("## V1 除湿-节能矛盾：固定 Tes 扫描（迈阿密夏季设计日 ×3 天，满足度 1.0）\n")
    rows = []
    for tes in (5.0, 7.0, 10.0, 13.0):
        r = run_scenario("B", "miami", days=3.0, target=tes, **BASE)
        row = {"Tes ℃": tes, **r.summary()}
        strip(row, "算法", "初始达温 min", "压缩机到峰值 min", "达温间隔 min")
        rows.append(row)
    add(md_table(rows))
    add("\n机理：Tes 抬高 → 盘管变暖 → SHR 升高 → 除湿量下降、RH 上升；同时 COP 提升省电。"
        "V1.44 往上学 Tes 时，除湿是它看不见的代价（外机无湿度传感）。\n")

    # ---- V2 除湿/达温 vs 负荷满足度 ----
    add("## V2 负荷满足度扫描（迈阿密，3 Ton，算法 B，Tes=7）\n")
    rows = []
    for sat in (0.7, 1.0, 1.3):
        r = run_scenario("B", "miami", days=3.0, tons=3.0, satisfaction=sat)
        row = {"满足度": f"{sat:.0%}", **r.summary()}
        strip(row, "算法", "初始达温 min", "压缩机到峰值 min")
        rows.append(row)
    add(md_table(rows))
    add("\n机理：满足度高（能力过剩）→ 循环短 → 盘管湿润时间占比低 + 停机再蒸发次数多 → 除湿差；"
        "满足度低 → 达温慢、高温时段拉不住。\n")

    # ---- V3 风量匹配（100% 基准值扫描） ----
    add("## V3 风量匹配基准扫描（休斯顿夏季设计日，算法 B，固定风挡 100%）\n")
    rows = []
    for cfm in (330.0, 400.0, 450.0):
        r = run_scenario("B", "houston", days=3.0, cfm_per_ton=cfm, **BASE)
        row = {"CFM/ton": cfm, **r.summary()}
        strip(row, "算法", "初始达温 min", "压缩机到峰值 min", "达温间隔 min")
        rows.append(row)
    add(md_table(rows))

    # ---- V4 达温慢分解 ----
    add("## V4 达温慢贡献分解（弗雷斯诺，设定阶跃 −1.5℃，满足度 1.0）\n")
    cases = [
        ("B + 快爬升", dict(kind="B", ramp_hz_s=1.5, start_hold_s=60)),
        ("B + 默认爬升", dict(kind="B")),
        ("C V1.44 + 快爬升", dict(kind="C", ramp_hz_s=1.5, start_hold_s=60)),
        ("C V1.44 + 默认爬升", dict(kind="C")),
        ("A 定频", dict(kind="A")),
    ]
    for scen, label in (("summer_design", "4a 设计日"),
                        ("summer_typical", "4b 典型夏日（负荷比设计日低 → 有裕度）")):
        add(f"### {label}\n")
        q_pre = calibrate_q("fresno", scen, **BASE)
        rows = []
        for name, kw in cases:
            kw = dict(kw)
            kind = kw.pop("kind")
            if kind == "C":
                kw["prelearn_q"] = q_pre
            r = run_setpoint_step(kind, "fresno", scenario=scen, **BASE, **kw)
            s = r.summary()
            rows.append({"方案": name,
                         "阶跃后达温 min": s["初始达温 min"],
                         "压缩机到峰值 min": s["压缩机到峰值 min"],
                         "平均 t2 min": s["平均运转 t2 min"],
                         "日耗电 kWh": s["日耗电 kWh"],
                         "出带 %": s["出带时间 %"]})
        add(md_table(rows))
    add("\n读法：B−快爬升 与 B−默认 之差 = 压缩机爬坡贡献；C 与 B 之差 = 算法行为贡献；"
        "对照 V2 可见满足度的支配作用。\n")

    # ---- V5 制热 ----
    add("## V5 制热模式（×3 天，满足度按制热负荷锚定 1.0）\n")
    st5 = {}
    for loc5, scen, label in (
            ("boston", "winter_design", "5a 波士顿冬季设计日"),
            ("houston", "winter_typical", "5b 休斯顿典型冬日（V1.44 Tcs 学习生效）")):
        add(f"### {label}\n")
        q_h = calibrate_q(loc5, scen, **BASE)
        rows = []
        for kind, kw in (("A", {}), ("B", {}), ("C", {"prelearn_q": q_h})):
            r = run_scenario(kind, loc5, scenario=scen, days=3.0, **BASE, **kw)
            row = r.summary()
            strip(row, "初始达温 min")
            if kind == "C":
                st5 = learner_state(r.algo)
            rows.append(row)
        add(md_table(rows))
    add(f"\n5b 学习状态：`{json.dumps(st5, ensure_ascii=False)}`\n")

    # ---- V6 保温档（不加满足度锚定——展示保温的真实绝对影响） ----
    add("## V6 保温档影响（达拉斯夏季设计日，3 Ton，不做满足度归一）\n")
    rows = []
    for ins in ("poor", "medium", "good"):
        q_pre6 = calibrate_q("dallas", insulation=ins, tons=3.0)
        r = run_scenario("C", "dallas", days=3.0, insulation=ins, tons=3.0,
                         prelearn_q=q_pre6)
        dl6 = design_load("dallas", ins)["total_w"]
        row = {"保温": ins, "自然配比": round(3 * TON_W / dl6, 2), **r.summary()}
        strip(row, "算法", "压缩机到峰值 min")
        rows.append(row)
    add(md_table(rows))

    # ---- V7 TA 扫描 ----
    add("## V7 目标运转时间 TA 扫描（休斯顿，V1.44 预学习=标定负荷，满足度 1.0）\n")
    q_pre7 = calibrate_q("houston", **BASE)
    rows = []
    for ta in (30.0, 60.0, 90.0):
        r = run_scenario("C", "houston", days=3.0, prelearn_q=q_pre7,
                         ta_min=ta, **BASE)
        row = {"TA min": ta, **r.summary()}
        strip(row, "算法", "初始达温 min", "压缩机到峰值 min")
        rows.append(row)
    r = run_scenario("C", "houston", days=3.0, ta_min=60.0,
                     prelearn_q=q_pre7 * 1.4, **BASE)
    row = {"TA min": "60（预学习偏高 40%）", **r.summary()}
    strip(row, "算法", "初始达温 min", "压缩机到峰值 min")
    rows.append(row)
    add(md_table(rows))
    add("\n对照行：预学习负荷初值偏高 → 目标总负荷高估 → 持续 UP → Tes 锁死下限，节能失效。\n")

    # ---- V8 冷启动学习 ----
    add("## V8 冷启动学习（休斯顿典型夏日 ×7 天，无预学习，满足度 1.0）\n")
    r = run_scenario("C", "houston", scenario="summer_typical", days=7.0, **BASE)
    row = r.summary()
    st = learner_state(r.algo)
    row["已学成箱数"] = len(st["learned_bins"])
    add(md_table([row]))
    add(f"\n学习状态：`{json.dumps(st, ensure_ascii=False)}`\n")

    # ---- V9 内机风量模式 ----
    add("## V9 内机风量三模式对比（迈阿密夏季设计日 ×3 天，V1.44 预学习，满足度 1.0）\n")
    q_pre9 = calibrate_q("miami", **BASE)
    add("### 9a 三种模式\n")
    rows = []
    for name, kw in (("固定风挡 100%", dict(fan_ctrl="fixed")),
                     ("两档风·1档 70%", dict(fan_ctrl="two", two_stage=1)),
                     ("两档风·2档 100%", dict(fan_ctrl="two", two_stage=2)),
                     ("自动风（60%起/50min/+10%每10min）", dict(fan_ctrl="auto"))):
        r = run_scenario("C", "miami", days=3.0, prelearn_q=q_pre9, **BASE, **kw)
        row = {"风量模式": name, **r.summary()}
        strip(row, "算法", "初始达温 min")
        rows.append(row)
    add(md_table(rows))
    add("\n### 9b 自动风参数：初始保持时长扫描（其余默认）\n")
    rows = []
    for wait in (30.0, 50.0, 70.0):
        r = run_scenario("C", "miami", days=3.0, prelearn_q=q_pre9,
                         fan_ctrl="auto", auto_wait_s=wait * 60, **BASE)
        row = {"保持 min": wait, **r.summary()}
        strip(row, "算法", "初始达温 min", "压缩机到峰值 min")
        rows.append(row)
    add(md_table(rows))
    add("\n### 9c 自动风初始风量扫描（其余默认）\n")
    rows = []
    for init in (0.50, 0.60, 0.80):
        r = run_scenario("C", "miami", days=3.0, prelearn_q=q_pre9,
                         fan_ctrl="auto", auto_init=init, **BASE)
        row = {"初始风量": f"{init:.0%}", **r.summary()}
        strip(row, "算法", "初始达温 min", "压缩机到峰值 min")
        rows.append(row)
    add(md_table(rows))
    add("\n机理：低风量段盘管更冷 → 除湿强、显热弱；变频机会用频率部分补偿。"
        "自动风在循环前段低风量强除湿，长循环未达温时逐步提风量保能力——"
        "除湿与达温之间的折中由 4 个参数决定。\n")

    # ---- V10 湿源变量（新风 / 产湿；不做满足度归一以展示真实增量） ----
    add("## V10 湿源变量扫描（迈阿密夏季设计日 ×3 天，3 Ton，算法 B，Tes=7）\n")
    add("### 10a 机械新风量（湿量=换气量×(W_out−W_in)，同时带入显热）\n")
    rows = []
    for vent in (0.0, 120.0, 240.0):
        r = run_scenario("B", "miami", days=3.0, tons=3.0, vent_m3h=vent)
        row = {"新风 m³/h": vent, **r.summary()}
        strip(row, "算法", "初始达温 min", "压缩机到峰值 min", "达温间隔 min")
        rows.append(row)
    add(md_table(rows))
    add("\n### 10b 人员/生活产湿（kg/h）\n")
    rows = []
    for mg in (0.3, 1.0, 2.0):
        r = run_scenario("B", "miami", days=3.0, tons=3.0, moist_gain_kgh=mg)
        row = {"产湿 kg/h": mg, **r.summary()}
        strip(row, "算法", "初始达温 min", "压缩机到峰值 min", "达温间隔 min")
        rows.append(row)
    add(md_table(rows))
    add("\n说明：两路湿源保持各自物理单位（新风/渗透按换气量×室外露点，产湿按 kg/h），"
        "不折算成无物理意义的比例；本组不做满足度归一，以展示湿源增加的真实负荷与 RH 影响。\n")

    out = Path(__file__).resolve().parent.parent / "docs" / "validation-tables.md"
    out.write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"\n[written] {out}")


if __name__ == "__main__":
    main()
