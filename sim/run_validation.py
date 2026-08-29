"""运行全部验证场景并生成 docs/validation-report.md 的数据表。

用法：python -m sim.run_validation
"""

from __future__ import annotations

import json
from pathlib import Path

from .algorithms import V144Learner
from .scenarios import (s1_design_day, s2_summer_week, s3_mild_season,
                        s4_setpoint_step, run_scenario)
from .models import Weather


def calibrate_q(weather: Weather, t_set: float = 24.5, days: float = 2.0) -> float:
    """用算法 B 在给定气候下运行，标定平均总负荷（W，冷媒侧）作预学习初值。"""
    r = run_scenario("B", days=days, weather=weather, t_set=t_set)
    return r.cool_kwh / (r.days * 24) * 1000 / 0.75


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
    tes = {b: round(v, 2) for b, v in sorted(algo._tes_store.items())}
    return {"learned_bins": bins, "tes_store": tes,
            "signals": len(algo.signals_log)}


def main() -> None:
    report: list[str] = []
    add = report.append

    # ---- S1 设计日稳态（35℃ 恒定；C 预学习） ----
    add("## S1 设计日稳态（恒定 35℃，V1.44 预学习完成）\n")
    # 预学习 q 标定：以固定 Tes 在同一气候下跑一遍取平均负荷
    q_cal = calibrate_q(Weather(t_mean=35.0, t_amp=0.0, solar_peak_w=2000.0))
    rows = []
    for kind, kw in (("A", {}), ("B", {}), ("C", {"prelearn_q": q_cal})):
        r = s1_design_day(kind, **kw)
        rows.append(r.summary())
        if kind == "C":
            s1_state = learner_state(r.algo)
    add(f"预学习负荷标定值：q ≈ {q_cal:.0f} W（由算法 B 运行结果标定）\n")
    add(md_table(rows))
    add(f"\nV1.44 学习器状态：`{json.dumps(s1_state, ensure_ascii=False)}`\n")

    # ---- S2 典型夏周（冷启动学习收敛） ----
    add("## S2 典型夏周（30±5℃ × 7 天，V1.44 冷启动学习）\n")
    rows = []
    for kind in ("A", "B", "C"):
        r = s2_summer_week(kind)
        row = r.summary()
        if kind == "C":
            st = learner_state(r.algo)
            row["已学成箱数"] = len(st["learned_bins"])
            s2_state = st
            # 前 2 天 vs 后 2 天对比（学习期 vs 学成后）
            early = [c for c in r.cycles if c.t_on < 12 * 3600 + 2 * 86400]
            late = [c for c in r.cycles if c.t_on >= 12 * 3600 + 5 * 86400]
            mean_t2 = lambda cs: (sum(c.t2 for c in cs if c.t2 > 0)
                                  / max(sum(1 for c in cs if c.t2 > 0), 1) / 60)
            s2_early_t2, s2_late_t2 = mean_t2(early), mean_t2(late)
        rows.append(row)
    add(md_table(rows))
    add(f"\nV1.44 学习状态：`{json.dumps(s2_state, ensure_ascii=False)}`\n")
    add(f"\nV1.44 前 2 天平均 t2 = {s2_early_t2:.1f} min，"
        f"第 6~7 天平均 t2 = {s2_late_t2:.1f} min\n")

    # ---- S3 轻负荷季 ----
    add("## S3 轻负荷季（24±4℃，设定 23℃）\n")
    rows = []
    for kind in ("A", "B", "C"):
        r = s3_mild_season(kind)
        row = r.summary()
        if kind == "C":
            st = learner_state(r.algo)
            row["已学成箱数"] = len(st["learned_bins"])
        rows.append(row)
    add(md_table(rows))
    add("\n关注点：长循环触发 t1≥120min / t2≥240min 丢样判据 → 学成箱数少（缺点 2.2-8 复现）。\n")

    # ---- S4 设定阶跃 pull-down ----
    add("## S4 设定阶跃（25.5→24.0℃，观察 pull-down 达温时间）\n")
    q_cal4 = calibrate_q(Weather(t_mean=32.0, t_amp=4.0), t_set=25.5)
    rows = []
    for kind, kw in (("A", {}), ("B", {}), ("C", {"prelearn_q": q_cal4})):
        r = s4_setpoint_step(kind, **kw)
        s = r.summary()
        s["阶跃后达温 min"] = s.pop("初始达温 min")
        rows.append(s)
    add(md_table(rows))

    # ---- S5 参数扫描 ----
    add("## S5 参数扫描（V1.44，典型夏日 30±5℃ × 3 天，预学习完成）\n")
    w = lambda: Weather(t_mean=30.0, t_amp=5.0)
    q_cal5 = calibrate_q(w())
    add(f"预学习负荷标定值：q ≈ {q_cal5:.0f} W\n")
    add("### 5a 目标运转时间 TA\n")
    rows = []
    for ta in (30, 45, 60, 90):
        r = run_scenario("C", days=3.0, weather=w(), prelearn_q=q_cal5, ta_min=ta)
        row = r.summary()
        row = {"TA min": ta, **row}
        row.pop("算法")
        rows.append(row)
    add(md_table(rows))
    add("\n### 5b 温控器回差（算法不可见的外部件差异）\n")
    rows = []
    for db in (0.5, 1.0, 2.0):
        r = run_scenario("C", days=3.0, weather=w(), prelearn_q=q_cal5,
                         deadband_f=db)
        row = r.summary()
        row = {"回差 °F": db, **row}
        row.pop("算法")
        rows.append(row)
    add(md_table(rows))
    add("\n### 5c 能力测量偏差（Gr×Δh 误差敏感度）\n")
    rows = []
    for bias in (-0.15, 0.0, 0.15):
        r = run_scenario("C", days=3.0, weather=w(), prelearn_q=q_cal5,
                         meas_bias=bias)
        row = r.summary()
        row = {"测量偏差": f"{bias:+.0%}", **row}
        row.pop("算法")
        rows.append(row)
    add(md_table(rows))

    out = Path(__file__).resolve().parent.parent / "docs" / "validation-tables.md"
    out.write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"\n[written] {out}")


if __name__ == "__main__":
    main()
