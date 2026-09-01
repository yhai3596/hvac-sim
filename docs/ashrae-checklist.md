# ASHRAE 内置气候值复核清单

`sim/climate.py` 里 8 个代表站的设计工况是**内置值**，来源标注为 ANSI/ASHRAE Standard 169 /
Fundamentals 设计工况。开发环境的网络出不去（`ashrae-meteo.info` 实测返回 `000` 超时），
**我无法替你在线核对**。对外发布任何用到绝对量的结论之前，请按本清单逐格核对。

## 怎么核

1. 打开 <https://ashrae-meteo.info/>（或 ASHRAE Handbook—Fundamentals 第 14 章的设计工况表）
2. 查每个站点对应的气象站，取下列口径的值：
   - **夏季干球 0.4%**：Cooling DB/MCWB 表的 0.4% 干球
   - **夏季湿球**：同一行的 MCWB（同期湿球）
   - **夏季露点**：Dehumidification DP/MCDB 表的 0.4% 露点
   - **冬季干球 99.6%**：Heating DB 表的 99.6% 干球
   - **日较差**：Mean Daily Range
3. 与下表"内置值"比对，差异超过 **±0.5℃** 就记下来，一起发我改
4. 全部核完后，把 `README.md` 与 `docs/validation-report.md` 里的"需复核"声明改成"已核对（日期 / 数据版本）"

`solar_peak_w`（晴天正午水平面日射峰值）不是 ASHRAE 设计工况表里的量，是按纬度与季节估的
量级值，只影响负荷构成的比例，**不必逐站核**——但若你手上有当地实测辐照，替换会更准。

## 待核对照表

| 站点 | 气候区 | 夏季干球 0.4%<br>(℃) | 夏季湿球<br>(℃) | 夏季露点<br>(℃) | 冬季干球 99.6%<br>(℃) | 夏季日较差<br>(K) | 核对结果 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 洛杉矶 CA（沿海）<br>`los_angeles` | 3B | 29.4 | 17.9 | 15.0 | 7.2 | 7.0 | ☐ |
| 弗雷斯诺 CA（内陆）<br>`fresno` | 3B | 40.0 | 21.1 | 12.0 | -0.6 | 17.0 | ☐ |
| 休斯顿 TX<br>`houston` | 2A | 36.1 | 25.0 | 23.5 | -1.1 | 10.0 | ☐ |
| 达拉斯 TX<br>`dallas` | 3A | 38.9 | 23.9 | 21.0 | -6.1 | 11.0 | ☐ |
| 迈阿密 FL<br>`miami` | 1A | 33.3 | 25.6 | 24.0 | 8.9 | 6.7 | ☐ |
| 奥兰多 FL<br>`orlando` | 2A | 34.4 | 24.4 | 23.5 | 3.9 | 9.0 | ☐ |
| 波士顿 MA<br>`boston` | 5A | 32.8 | 22.8 | 19.0 | -13.9 | 8.9 | ☐ |
| 纽约 NY<br>`new_york` | 4A | 32.8 | 23.3 | 20.0 | -10.6 | 7.5 | ☐ |

共 8 站 × 5 个数值 = **40 格**，半小时能核完。

## 核完之后

改动只需编辑 `sim/climate.py` 里的 `LOCATIONS`，然后跑：

```bash
python3 -m sim.test_sim          # 17 项单测，确认没改坏结构
python3 -m sim.run_validation    # 重新生成 docs/validation-tables.md
```

网页端的同名数值在 `web/index.html` 的 `LOC` 表里，两处必须一起改——JS 与 Python 是同一
物理模型的双实现，数值不一致会让交叉验证失去意义。
