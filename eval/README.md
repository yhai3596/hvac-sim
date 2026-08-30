# 智能助手 LLM 解析路径评测

评的是这条链路：**用户自然语言 → LLM 按平台接口契约编译 → 工作台校验 → 真的跑起来 → 出结果**。
不是评内置正则兜底（那条只是无 API 时的降级路径）。

## 为什么要单独评这条路

助手有三条解析入口：粘贴计划 JSON、LLM 按契约编译、内置正则兜底。**LLM 是主路径**——
自然语言的写法是无穷的，正则永远追不上（两轮对抗审计已经证明了这点）。但主路径的正确性
取决于两件事：给模型的接口契约够不够精确，以及模型的输出能不能被平台直接执行。这套评测
就是量这两件事。

## 跑法

```bash
# 打真实 API（推荐）
node eval/run_eval.js --base https://api.deepseek.com --model deepseek-chat --key sk-xxx
node eval/run_eval.js --base https://api.anthropic.com --protocol anthropic \
                      --model claude-opus-5 --key sk-ant-xxx

# 走 serve.py --proxy 的同源反代（Key 留在服务端）
node eval/run_eval.js --base http://127.0.0.1:8000/api/deepseek --model deepseek-chat --key proxy

# 额外把每个计划真的跑一遍，验证可执行性（慢一些）
node eval/run_eval.js --base ... --key ... --execute

# 离线回归：用事先存好的模型回复
node eval/run_eval.js --responses eval/responses.json
node eval/run_eval.js --corpus corpus-bad.json --responses eval/responses-bad.json   # 反向对照
```

需要 playwright（评测要在真实页面里调 `buildParsePrompt` 与 `validatePlan`）：
`NODE_PATH=<装了 playwright 的 node_modules> node eval/run_eval.js ...`

## 七个评分维度

| 维度 | 判定方式 | 为什么重要 |
| --- | --- | --- |
| JSON 结构合法 | 能解析出 JSON 且含 compare/conditions | 输出直接驱动程序，不能是散文 |
| 键名合法 | 校验层未报"忽略未知变量" | 模型编造键名 = 那部分需求静默丢失 |
| 取值在范围内 | 校验层未报越界截取 | 越界值被夹紧后，多个臂可能变成同一个 |
| 单变量纪律 | 臂之间、条件档内只改一个"根变量" | 一次改两个变量的对比无法归因 |
| 物理有效性 | 没有等价/重复的臂（平台一旦出手修复即判失败） | 等价臂跑出重合曲线，会坐实假结论 |
| 规模合理 | 方案数在预期上限内 | 交叉相乘会让方案数爆炸 |
| 语义覆盖 | 用户点名的对比对象/维度/数值都在计划里 | 这是"听懂没有"的最终判据 |

外加 `--execute`：把计划真的送进 `runExperiment` 跑短窗口，证明产出的是**可执行指令**而非好看的 JSON。

## 反向对照（重要）

一个只会打满分的评分器没有价值。`responses-bad.json` 里注入了 10 种真实的 LLM 失败模式
（编造键名、取值越界、一个臂改两个变量、排出物理等价的臂、把制热的 Tcs 用在制冷、条件全
交叉导致方案爆炸、返回散文而非 JSON、忽略用户明写的预热时长、把输出指标当成条件维度、
把量词"档"误判成风量模式）。当前评分器在这 10 条上给出 **全部通过 0/10**，说明它抓得住错。

新增评分维度时，请同步往 `responses-bad.json` 加一条对应的反例。

## 当前基线（务必看清适用范围）

`eval/responses.json` 里的模型回复是 **Claude 在开发会话内充当被测模型**产生的，
不是对任何真实 API 的实测。开发环境没有外网出口。

因此这组 24/24（含可执行 24/24）**只能证明三件事**：
1. 接口契约是可表达的——一个遵守契约的模型能对全部 24 条语料产出合法且可执行的计划；
2. 评测脚手架本身工作正常，且对错误敏感（反向对照 0/10）；
3. 从提示词到跑出指标这条链路是通的。

它**不能**说明任何真实模型的水平——期望值和模型回答都出自同一方，属于自己给自己出题。
真实分数请用上面的 `--base/--key` 自行跑一遍；不同模型、不同版本的差距很可能很大，
这也正是这套评测存在的意义。

## 语料

`corpus.json` 的 24 条全部来自真实工程师口语，其中多数是两轮多 agent 对抗审计中
**曾经把解析器打挂**的句子。`expect` 断言的是"编译结果"而不是实现细节，因此同一套语料
可以用来横向比较不同模型。
