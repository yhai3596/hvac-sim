# 空调控制算法分析与仿真平台

面向北美替代市场（内外机无通讯）的空调控制算法研究：解读现有 V1.44 控制算法（负荷预测 + Tes/Tcs 自学习），分析其在"仅冷媒状态 + 温控器达温停机信号"约束下的优缺点，并搭建基于暖通行业成熟模型的仿真平台，用于不同算法与控制条件下的运行状态评估与算法参数调优。

**v2**：新增 ASHRAE 地点气候（加州/德州/佛州/麻省/纽约 8 个代表站，设计日/典型日）、房间湿平衡与除湿指标（针对"除湿不足"反馈）、显式压缩机频率动态与"到最大输出时间"指标（针对"达温慢"反馈）、制热模式（热泵能力衰减/化霜/辅助电热 + V1.44 Tcs 学习）、保温档、基准点方案对比工作流。

**v3**：能力段两档可选（3 Ton / 5 Ton）；负荷满足度变量（额定能力/建筑设计负荷，50~150%，锚定负荷总量，房屋参数决定负荷构成）；内机风量三模式——固定 100% / 两档风（1 档 70%、2 档 100%）/ 自动风（启动 60% 风量，50 min 未达温后每 10 min +10% 至 100%，四个参数均可调参与模拟）；新增 V9 风量模式对比与参数扫描。

## 内容导览

| 位置 | 内容 |
| --- | --- |
| [`docs/analysis.md`](docs/analysis.md) | 现有 V1.44 算法逻辑解读 + 优缺点分析 + 与 CC 参考方案的互补关系 |
| [`docs/simulation-plan.md`](docs/simulation-plan.md) | 仿真平台规划：模型架构、子模型依据（2R2C / EnergyPlus 风格 DX 曲线 / 温控器滞环）、评估指标、验证场景 |
| [`docs/validation-report.md`](docs/validation-report.md) | 仿真验证结果解读：算法 A/B/C 对比、缺点定量复现、参数扫描结论 |
| [`docs/validation-tables.md`](docs/validation-tables.md) | 由 `sim/run_validation.py` 自动生成的完整数据表 |
| [`sim/`](sim/) | Python 仿真核心（标准库实现，无第三方依赖） |
| [`web/index.html`](web/index.html) | 交互式可视化仿真台（浏览器内实时仿真、参数手调、动态曲线） |

## 快速开始

```bash
python3 -m sim.test_sim          # 单元测试（14 项）
python3 -m sim.run_validation    # 运行 V0~V8 验证场景，重新生成数据表
```

自定义实验：

```python
from sim.scenarios import run_scenario, run_setpoint_step

# 地点驱动：迈阿密夏季设计日，V1.44，改 TA/风量/回差
r = run_scenario("C", "miami", scenario="summer_design", days=3.0,
                 prelearn_q=5000, ta_min=45, cfm_per_ton=350, deadband_f=1.0)
print(r.summary())

# 设定阶跃 pull-down 实验
r = run_setpoint_step("C", "fresno", scenario="summer_typical", prelearn_q=3800)

# 制热：波士顿典型冬日
r = run_scenario("C", "boston", scenario="winter_typical", days=3.0)
```

可视化页面：直接用浏览器打开 `web/index.html`（或部署为静态页）。JS 端与 Python 端为同一物理模型的双实现，已做数值交叉验证（迈阿密设计日工况下达温、循环统计、能耗、湿度指标逐项一致）。

## 对比算法

- **A 定频 on/off 基线**：北美被替代对象（单级机组）
- **B 变频固定目标**：变频硬件、固定 Tes/Tcs 不学习
- **C V1.44 复刻**：外温分箱 × 昼/夜负荷系数学习（7 天采样中心值法 + 70% 信赖区间）、目标运转时间 TA、能力恒定外推预测、UP/DOWN 滞环信号、Tes/Tcs 分箱保存（制冷/制热双模式）

算法层严格遵守信息边界：只能看到 thermo-on/off 时序、室外温度与外机自测能力（可注入 Gr×Δh 测量偏差）；室温、湿度、设定值、回差仅对温控器/房间模型可见。

## 数据来源声明

地点设计工况为内置 ASHRAE 值（ANSI/ASHRAE Standard 169 / Fundamentals 设计工况，见 `sim/climate.py` 顶部说明）。开发环境网络受限无法在线核对，发布前请对照 [ashrae-meteo.info](https://ashrae-meteo.info/) 或 ASHRAE 官网复核（每站 5 个数值）。
