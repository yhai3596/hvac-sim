"""运行全部验证场景并生成 docs/validation-tables.md。

场景矩阵围绕两项客户反馈组织：
- 达温慢：算法行为 × 压缩机爬坡 × 容量配比 的贡献分解
- 除湿不足：Tes 高度 × 容量配比（短循环） × 风量匹配 的贡献分解

用法：python3 -m sim.run_validation
"""

from __future__ import annotations

import json
from pathlib import Path

from .algorithms import V144Learner
from .scenarios import run_scenario, run_setpoint_step, design_load
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
    add("地点设计工况为内置 ASHRAE 值（见 sim/climate.py 顶部说明，发布前请复核）。\n")

    # ---- V0 地点设计负荷一览 ----
    add("## V0 地点设计负荷（普通保温、7kW 额定 → 容量配比）\n")
    rows = []
    for key, loc in LOCATIONS.items():
        dl = design_load(key)
        rows.append({
            "地点": loc.name, "气候区": loc.zone,
            "夏设计干球 ℃": loc.summer_db04, "湿球 ℃": loc.summer_wb,
            "冬设计干球 ℃": loc.winter_db996,
            "显热 kW": round(dl["sensible_w"] / 1000, 2),
            "潜热 kW": round(dl["latent_w"] / 1000, 2),
            "制冷配比": round(7000 / dl["total_w"], 2),
            "制热配比": round(7600 / max(dl["heating_w"], 1) , 2),
        })
    add(md_table(rows))

    # ---- V1 除湿 vs Tes（迈阿密，固定目标扫描） ----
    add("\n## V1 除湿-节能矛盾：固定 Tes 扫描（迈阿密夏季设计日 ×3 天）\n")
    rows = []
    for tes in (5.0, 7.0, 10.0, 13.0):
        r = run_scenario("B", "miami", days=3.0, target=tes)
        row = {"Tes ℃": tes, **r.summary()}
        strip(row, "算法", "初始达温 min", "压缩机到峰值 min", "达温间隔 min")
        rows.append(row)
    add(md_table(rows))
    add("\n机理：Tes 抬高 → 盘管变暖 → SHR 升高 → 除湿量下降、RH 上升；同时 COP 提升省电。"
        "V1.44 往上学 Tes 时，除湿是它看不见的代价（外机无湿度传感）。\n")

    # ---- V2 除湿 vs 容量配比（短循环再蒸发） ----
    add("## V2 除湿 vs 容量配比（迈阿密，算法 B，Tes=7）\n")
    rows = []
    dl_miami = design_load("miami")["total_w"]
    for qr in (5300.0, 7000.0, 10500.0):
        r = run_scenario("B", "miami", days=3.0, q_rated=qr)
        row = {"额定 kW": qr / 1000, "配比": round(qr / dl_miami, 2), **r.summary()}
        strip(row, "算法", "初始达温 min", "压缩机到峰值 min")
        rows.append(row)
    add(md_table(rows))
    add("\n机理：配比越大 → 循环越短 → 盘管湿润时间占比低 + 停机再蒸发次数多 → 除湿差。\n")

    # ---- V3 除湿/能力 vs 风量匹配 ----
    add("## V3 风量匹配影响（休斯顿夏季设计日，算法 B）\n")
    rows = []
    for cfm in (330.0, 400.0, 450.0):
        r = run_scenario("B", "houston", days=3.0, cfm_per_ton=cfm)
        row = {"CFM/ton": cfm, **r.summary()}
        strip(row, "算法", "初始达温 min", "压缩机到峰值 min", "达温间隔 min")
        rows.append(row)
    add(md_table(rows))
    add("\n机理：风量低 → 盘管冷（Te 低）→ 除湿强但显热能力与 COP 降；风量高反之。\n")

    # ---- V4 达温慢分解（设定阶跃，分设计日/典型日两种工况） ----
    add("## V4 达温慢贡献分解（弗雷斯诺，设定阶跃 −1.5℃）\n")
    q_pre = design_load("fresno")["total_w"]
    cases = [
        ("B + 快爬升", dict(kind="B", ramp_hz_s=1.5, start_hold_s=60)),
        ("B + 默认爬升", dict(kind="B")),
        ("C V1.44 + 快爬升", dict(kind="C", ramp_hz_s=1.5, start_hold_s=60,
                                  prelearn_q=q_pre)),
        ("C V1.44 + 默认爬升", dict(kind="C", prelearn_q=q_pre)),
        ("A 定频", dict(kind="A")),
    ]
    for scen, label in (("summer_design", "4a 设计日（配比≈0.97，容量吃紧）"),
                        ("summer_typical", "4b 典型夏日（有容量裕度）")):
        add(f"### {label}\n")
        rows = []
        for name, kw in cases:
            kw = dict(kw)
            kind = kw.pop("kind")
            if kind == "C" and scen == "summer_typical":
                kw["prelearn_q"] = q_pre * 0.6   # 预学习值按典型日负荷折算
            r = run_setpoint_step(kind, "fresno", scenario=scen, **kw)
            s = r.summary()
            rows.append({"方案": name,
                         "阶跃后达温 min": s["初始达温 min"],
                         "压缩机到峰值 min": s["压缩机到峰值 min"],
                         "平均 t2 min": s["平均运转 t2 min"],
                         "日耗电 kWh": s["日耗电 kWh"],
                         "出带 %": s["出带时间 %"]})
        add(md_table(rows))
    add("\n读法：B−快爬升 与 B−默认 之差 = 压缩机爬坡贡献；C 与 B 之差 = 算法行为贡献；"
        "4a 与 4b 之差显示容量配比的支配作用（设计日容量吃紧时，算法/爬坡差异被容量瓶颈掩盖）。\n")

    # ---- V5 制热 ----
    add("## V5 制热模式（×3 天）\n")
    for loc5, scen, label in (
            ("boston", "winter_design",
             "5a 波士顿冬季设计日（-13.9℃：热泵+8kW 辅热仍不足，连续运转）"),
            ("boston", "winter_typical",
             "5b 波士顿典型冬日（热泵仍无法独立满足，辅热循环顶温——寒区一拖一配比的现实）"),
            ("houston", "winter_typical",
             "5c 休斯顿典型冬日（2A 轻制热负荷，正常循环，V1.44 Tcs 学习生效）")):
        add(f"### {label}\n")
        q_h = calibrate_q(loc5, scen)
        rows = []
        for kind, kw in (("A", {}), ("B", {}), ("C", {"prelearn_q": q_h})):
            r = run_scenario(kind, loc5, scenario=scen, days=3.0, **kw)
            row = r.summary()
            strip(row, "初始达温 min")
            if kind == "C":
                st5 = learner_state(r.algo)
            rows.append(row)
        add(md_table(rows))
    add(f"\n5c 学习状态：`{json.dumps(st5, ensure_ascii=False)}`\n")

    # ---- V6 保温档（达拉斯） ----
    add("## V6 保温档影响（达拉斯夏季设计日，V1.44 预学习）\n")
    rows = []
    for ins in ("poor", "medium", "good"):
        q_pre6 = calibrate_q("dallas", insulation=ins)
        r = run_scenario("C", "dallas", days=3.0, insulation=ins,
                         prelearn_q=q_pre6)
        dl6 = design_load("dallas", ins)["total_w"]
        row = {"保温": ins, "配比": round(7000 / dl6, 2), **r.summary()}
        strip(row, "算法", "压缩机到峰值 min")
        rows.append(row)
    add(md_table(rows))

    # ---- V7 TA 扫描（休斯顿，兼看除湿） ----
    add("## V7 目标运转时间 TA 扫描（休斯顿，V1.44 预学习=标定负荷）\n")
    q_pre7 = calibrate_q("houston")
    add(f"标定平均负荷 ≈ {q_pre7:.0f} W（设计负荷估算为 "
        f"{design_load('houston')['total_w']:.0f} W——用设计值当预学习初值会因目标"
        f"总负荷高估导致持续 UP、Tes 锁死下限，见下对照行）\n")
    rows = []
    for ta in (30.0, 60.0, 90.0):
        r = run_scenario("C", "houston", days=3.0, prelearn_q=q_pre7, ta_min=ta)
        row = {"TA min": ta, **r.summary()}
        strip(row, "算法", "初始达温 min", "压缩机到峰值 min")
        rows.append(row)
    r = run_scenario("C", "houston", days=3.0,
                     prelearn_q=design_load("houston")["total_w"], ta_min=60.0)
    row = {"TA min": "60（预学习偏高 36%）", **r.summary()}
    strip(row, "算法", "初始达温 min", "压缩机到峰值 min")
    rows.append(row)
    add(md_table(rows))

    # ---- 学习器状态样例（休斯顿冷启动 7 天） ----
    add("## V8 冷启动学习（休斯顿典型夏日 ×7 天，无预学习）\n")
    r = run_scenario("C", "houston", scenario="summer_typical", days=7.0)
    row = r.summary()
    st = learner_state(r.algo)
    row["已学成箱数"] = len(st["learned_bins"])
    add(md_table([row]))
    add(f"\n学习状态：`{json.dumps(st, ensure_ascii=False)}`\n")

    out = Path(__file__).resolve().parent.parent / "docs" / "validation-tables.md"
    out.write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"\n[written] {out}")


if __name__ == "__main__":
    main()
