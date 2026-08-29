# 空调控制算法分析与仿真平台

面向北美替代市场（内外机无通讯）的空调控制算法研究：解读现有 V1.44 控制算法（负荷预测 + Tes/Tcs 自学习），分析其在"仅冷媒状态 + 温控器达温停机信号"约束下的优缺点，并搭建基于暖通行业成熟模型的仿真平台，用于不同算法与控制条件下的运行状态评估（达温时间、停机时间、循环次数、能耗、舒适度）与算法参数调优。

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
python3 -m sim.test_sim          # 单元测试（7 项）
python3 -m sim.run_validation    # 运行全部验证场景，重新生成数据表
```

自定义实验：

```python
from sim.scenarios import run_scenario
from sim.models import Weather

r = run_scenario("C", days=5.0, weather=Weather(t_mean=32, t_amp=6),
                 ta_min=45, deadband_f=1.5, meas_bias=0.05, prelearn_q=4000)
print(r.summary())
```

可视化页面：直接用浏览器打开 `web/index.html`（或部署为静态页）。JS 端与 Python 端为同一物理模型的双实现，已做数值交叉验证（S1 工况下达温时间、循环统计、能耗逐项一致）。

## 对比算法

- **A 定频 on/off 基线**：北美被替代对象（单级机组）
- **B 变频固定 Tes**：变频硬件、不自学习
- **C V1.44 复刻**：外温分箱 × 昼/夜负荷系数学习（7 天采样中心值法 + 70% 信赖区间）、目标运转时间 TA、能力恒定外推预测、UP/DOWN 滞环信号、Tes 分箱保存

算法层严格遵守信息边界：只能看到 thermo-on/off 时序、室外温度与外机自测能力（可注入 Gr×Δh 测量偏差）；室温、设定值、回差仅对温控器模型可见。
