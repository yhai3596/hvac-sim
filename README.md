# 空调控制算法分析与仿真平台

面向北美替代市场（内外机无通讯）的空调控制算法研究：解读现有 V1.44 控制算法（负荷预测 + Tes/Tcs 自学习），分析其在"仅冷媒状态 + 温控器达温停机信号"约束下的优缺点，并搭建基于暖通行业成熟模型的仿真平台，用于不同算法与控制条件下的运行状态评估与算法参数调优。

**v2**：新增 ASHRAE 地点气候（加州/德州/佛州/麻省/纽约 8 个代表站，设计日/典型日）、房间湿平衡与除湿指标（针对"除湿不足"反馈）、显式压缩机频率动态与"到最大输出时间"指标（针对"达温慢"反馈）、制热模式（热泵能力衰减/化霜/辅助电热 + V1.44 Tcs 学习）、保温档、基准点方案对比工作流。

**v3**：能力段两档可选（3 Ton / 5 Ton）；负荷满足度变量（额定能力/建筑设计负荷，50~150%，锚定负荷总量，房屋参数决定负荷构成）；内机风量三模式——固定 100% / 两档风（1 档 70%、2 档 100%）/ 自动风（启动 60% 风量，50 min 未达温后每 10 min +10% 至 100%，四个参数均可调参与模拟）；新增 V9 风量模式对比与参数扫描。

**v4**：方案对比新增「指标说明」（13 项指标定义/单位/解读）；「LLM 方案分析报告」——把方案记录与指标定义组装为提示词调用大模型生成对比分析，支持多 API 配置与轮询（预置 Claude/Anthropic、OpenAI、DeepSeek、智谱 GLM、Kimi、MiniMax，均可测试连通性，失败自动切换），无 API 时可一键复制完整分析提示词；示例报告见 [`docs/sample-llm-report.md`](docs/sample-llm-report.md)。注意：Artifact 预览环境拦截外部网络请求，外部 API 调用需本地打开 `web/index.html` 或自行部署。

**v5**：湿源三路独立变量——人员/生活产湿（kg/h，运行中可调模拟人员活动）、渗透（ACH×室外露点）、新增机械新风量（m³/h×室外露点，同时带入显热），单位各自物理、不折算比例；满足度缩放修正为只作用显热源，湿源保持滑杆面值；新增 V10 湿源扫描验证。

**v6**：智能实验助手——自然语言描述测试条件（例：「对比自动风和二档风在不同热负荷、湿度负荷、室内设定温度以及不同地区下的影响」），解析为 **主对比臂 × 条件维度** 的实验计划：臂间只差一个变量（受控对比），条件维度逐一调节且互不相乘，未提及的变量取默认值、未给数值的维度自动取代表档位。流程为**先评估、再执行**——先给出方案数/合计机时/按实测步率推算的预计耗时，计划 JSON 可当场编辑，确认后才批量仿真（进度与剩余时间可视、可停止、结束恢复原参数），结果自动记入方案对比表并生成**对比矩阵**（各条件下"基准臂 → 对比臂"的指标变化）与 LLM 分析报告。解析三路径：粘贴计划 JSON 直跑 / 已配置 API 轮询解析 / 内置规则（15 类对比轴 + 15 类条件维度，经多 agent 对抗审计打磨，支持「二档风」「满足度 70% 和 130%」「LA 和休斯顿」「风量大一点小一点」「C算法跟B比」等真实口语写法），无法识别时给默认计划而非报错，另有「复制解析提示词」借助外部大模型的无 API 回退。

## 内容导览

| 位置 | 内容 |
| --- | --- |
| [`docs/analysis.md`](docs/analysis.md) | 现有 V1.44 算法逻辑解读 + 优缺点分析 + 与 CC 参考方案的互补关系 |
| [`docs/simulation-plan.md`](docs/simulation-plan.md) | 仿真平台规划：模型架构、子模型依据（2R2C / EnergyPlus 风格 DX 曲线 / 温控器滞环）、评估指标、验证场景 |
| [`docs/validation-report.md`](docs/validation-report.md) | 仿真验证结果解读：算法 A/B/C 对比、缺点定量复现、参数扫描结论 |
| [`docs/validation-tables.md`](docs/validation-tables.md) | 由 `sim/run_validation.py` 自动生成的完整数据表 |
| [`sim/`](sim/) | Python 仿真核心（标准库实现，无第三方依赖） |
| [`web/index.html`](web/index.html) | 交互式可视化仿真台（浏览器内实时仿真、参数手调、动态曲线） |
| [`web/serve.py`](web/serve.py) | 构建独立 HTML + 本地服务 + 可选的大模型 API 反向代理（零依赖） |
| [`docs/deploy.md`](docs/deploy.md) | 本地使用与服务器部署指南（含 CORS/API Key 的两种处理方式） |

## 快速开始

```bash
python3 web/serve.py             # 构建并在 http://127.0.0.1:8000 打开交互式仿真台
python3 -m sim.test_sim          # 单元测试（17 项）
python3 -m sim.run_validation    # 运行 V0~V10 验证场景，重新生成数据表
```

零第三方依赖：前端是 136 KB 单 HTML（无 CDN、无外链），仿真核心是纯标准库 Python。
本地使用、局域网共享、服务器部署与大模型 API 反代见 [`docs/deploy.md`](docs/deploy.md)。

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

可视化页面：`python3 web/serve.py`（会构建出自带 doctype 与 UTF-8 声明的 `dist/index.html` 并起本地服务）。
注意 `web/index.html` 是按 Artifact 约定写的正文片段，直接双击会进入 quirks 模式且编码靠浏览器猜——
中文 Windows 上易乱码，请用 `dist/index.html`。JS 端与 Python 端为同一物理模型的双实现，已做数值交叉验证。

## 对比算法

- **A 定频 on/off 基线**：北美被替代对象（单级机组）
- **B 变频固定目标**：变频硬件、固定 Tes/Tcs 不学习
- **C V1.44 复刻**：外温分箱 × 昼/夜负荷系数学习（7 天采样中心值法 + 70% 信赖区间）、目标运转时间 TA、能力恒定外推预测、UP/DOWN 滞环信号、Tes/Tcs 分箱保存（制冷/制热双模式）

算法层严格遵守信息边界：只能看到 thermo-on/off 时序、室外温度与外机自测能力（可注入 Gr×Δh 测量偏差）；室温、湿度、设定值、回差仅对温控器/房间模型可见。

## 数据来源声明

地点设计工况为内置 ASHRAE 值（ANSI/ASHRAE Standard 169 / Fundamentals 设计工况，见 `sim/climate.py` 顶部说明）。开发环境网络受限无法在线核对，发布前请对照 [ashrae-meteo.info](https://ashrae-meteo.info/) 或 ASHRAE 官网复核（每站 5 个数值）。
