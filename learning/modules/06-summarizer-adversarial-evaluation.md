# 06. 模块：报告合成、Red-Blue 对抗与评测

## 1. 模块职责

这个模块负责把“若干子任务结果”变成“可交付研究报告”，并尽量提高报告质量。

它分三层：

1. `SummarizerAgent`：把所有 `AgentResult` 合成 Markdown 报告。
2. `AdversarialLoop`：Red Agent 找问题，Blue Agent 修复。
3. `evaluation/`：用规则指标、Judge、benchmark、消融实验评估报告。

## 2. 关键文件

| 文件 | 作用 |
|---|---|
| `src/agents/summarizer.py` | 最终报告合成 |
| `src/adversarial/loop.py` | Red-Blue 循环控制 |
| `src/adversarial/red_agent.py` | 从事实性、逻辑、引用、覆盖等维度攻击报告 |
| `src/adversarial/blue_agent.py` | 根据问题修复报告 |
| `src/adversarial/verdict.py` | `Issue`、`RedVerdict`、`FixOperation`、评分引擎 |
| `evaluation/metrics/rule_based.py` | 规则指标：事实、幻觉、引用、逻辑、完备性 |
| `evaluation/metrics/stats.py` | bootstrap、Cohen's d、t-test |
| `evaluation/benchmarks/research_bench.py` | 自建 ResearchBench |
| `scripts/run_eval.py` | 运行评测 |
| `scripts/run_ablation.py` | 消融实验 |

## 3. SummarizerAgent 如何合成报告

`SummarizerAgent.run()` 输入：

```python
context = {
    "query": self._query,
    "results": self._results,
}
```

主流程：

```text
检查是否有子任务结果
按 confidence 排序子任务结果
构造 synthesis prompt
禁用 policy.tools
调用 LLM 生成 Markdown
解析引用来源和整体置信度
返回 AgentResult(output=ResearchReport)
```

注意这一句：

```python
old_tools = getattr(self.policy, "tools", None)
self.policy.tools = None
response = self.policy(messages)
self.policy.tools = old_tools
```

合成阶段不希望模型继续调用工具，所以临时禁用 tools。

## 4. 报告置信度怎么算

`_parse_report()` 中：

```python
llm_confidence = ...
success_rate = success / max(total, 1)
confidence = llm_confidence * (success_rate ** 0.5)
```

读法：

- LLM 自己会给一个 `Overall Confidence`。
- 系统再乘以子任务成功率的平方根。
- 如果子任务失败很多，最终置信度会被压低。

这个设计比完全相信 LLM 自评更稳。

## 5. 来源怎么提取

`SummarizerAgent._parse_report()` 会遍历每个 `AgentResult.trajectory`：

```python
for step in r.trajectory:
    if step.get("role") == "tool":
        ...
```

如果工具结果里有：

- `results` 列表，并且 item 里有 `url`
- 或 `papers` 列表，并且 paper 里有 `pdf_url`

就提取为 sources。

这里要注意一个项目缺点：它是从工具轨迹启发式提取来源，不等于报告中每句话都严格绑定引用。面试可以诚实讲这是未来可改进点。

## 6. AdversarialLoop 主流程

`AdversarialLoop.run(report)`：

```text
current = deepcopy(report)
for round in max_rounds:
    verdict = await red_agent.attack(current)
    检查已修复问题是否重新出现，判断震荡
    fixed_report, operations = await blue_agent.defend(current, verdict)
    记录评分、修复操作、stop_reason
    检查收敛条件
return current, history
```

它的停止条件：

- 达到最大轮数。
- overall score 达到阈值。
- 轮间分数变化小于 delta 阈值。
- 检测到已修复问题重新出现，判定震荡。

## 7. Orchestrator 什么时候进入对抗

`Orchestrator._do_synthesizing()` 合成完报告后：

```python
if self._config.enable_adversarial:
    return OrchestratorState.ADVERSARIAL
return OrchestratorState.DONE
```

`_do_adversarial()` 里：

```python
if report.confidence >= 0.8:
    跳过对抗
else:
    optimized_report, history = await self.adversarial_loop.run(report)
```

也就是说：配置开启，并且报告置信度不足时，才值得进入 Red-Blue。

## 8. 评测体系

规则评测在 `RuleBasedMetrics` 中：

| 指标 | 含义 |
|---|---|
| `fact_accuracy` | ground truth 关键词命中 |
| `semantic_fact_accuracy` | embedding 语义覆盖 |
| `hallucination_rate` | 无引用数字、绝对化表达、模糊引用等风险 |
| `citation_coverage` | 含引用段落比例 |
| `logical_consistency` | 简单矛盾检测 + 逻辑连接词 |
| `comprehensiveness` | expected topics 覆盖 |
| `composite_score` | 多指标加权综合 |
| `efficiency_score` | 轮数越少越高效 |

评测脚本：

- `scripts/run_eval.py`：标准评测。
- `scripts/run_ablation.py`：关闭某些模块看性能变化。
- `scripts/run_benchmark.py`：Agent vs baseline。
- `scripts/run_all_experiments.py`：批量实验。

## 9. 配置如何影响这个模块

`configs/default.yaml`：

```yaml
adversarial:
  enabled: true
  max_rounds: 10
  score_threshold: 9.5
  delta_threshold: 0.2
```

但 `runner.py` 初始化时有默认值：

```python
max_rounds=adversarial_cfg.get("max_rounds", 3)
score_threshold=adversarial_cfg.get("score_threshold", 8.0)
delta_threshold=adversarial_cfg.get("delta_threshold", 0.3)
```

如果 YAML 有值，以 YAML 为准。

模型分工：

```yaml
backend_mapping:
  summarizer: "deepseek"
  judge: "mimo"
  red_agent: "mimo"
  blue_agent: "mimo"
```

这说明报告合成用 DeepSeek，对抗审查和修复用 MiMo。

## 10. 需要掌握的 Python 语法

### 深拷贝

```python
current = copy.deepcopy(report)
```

对抗循环不直接修改原始报告，而是复制一份修。

### 元组返回

```python
return current, history
```

调用方：

```python
optimized_report, history = await self.adversarial_loop.run(report)
```

### 集合去重和交集

```python
resolved_issues: set[Issue] = set()
reappeared = resolved_issues.intersection(set(verdict.issues))
```

用于检测“已经修复的问题又出现了”，也就是震荡。

### 静态方法指标

`RuleBasedMetrics.fact_accuracy()` 是 `@staticmethod`，可以不实例化类直接调用。

## 11. 第一遍、第二遍、面试读法

第一遍：

- 看 `SummarizerAgent.run()`。
- 看 `_build_synthesis_prompt()`。
- 看 `AdversarialLoop.run()`。

第二遍：

- 看 `_parse_report()` 如何提取 confidence 和 sources。
- 看 `red_agent.py`、`blue_agent.py` 的 Prompt 和修复策略。
- 看 `rule_based.py` 每个指标怎么计算。

面试读法：

- 把它讲成“生成后质量控制”。
- 诚实说当前引用 grounding 是启发式，后续可以改成 evidence store。
- 评测体系是亮点，但不要夸成论文级完备，强调“工程可复现、可量化”。

## 12. 小练习

假设有 5 个子任务，3 个成功，LLM 自评 `Overall Confidence: 0.81`。

当前 Summarizer 计算置信度大概是：

```text
success_rate = 3 / 5 = 0.6
confidence = 0.81 * sqrt(0.6) ≈ 0.63
```

请问它是否会触发对抗？

参考答案：如果 `enable_adversarial=true`，会触发，因为 `0.63 < 0.8`。

## 13. 常见面试问法

Q：为什么需要 Red-Blue 对抗？

A：深度研究报告不是简单答案，容易出现事实漂移、引用不足、逻辑不一致和覆盖面缺失。Red Agent 专门攻击报告质量，Blue Agent 根据 verdict 修复，相当于在生成后加一层自动审稿和修订。

Q：Red-Blue 会不会无限循环？

A：不会。它有最大轮数、分数阈值、delta 收敛阈值和震荡检测。已修复问题如果重新出现，会记录震荡并提前结束。

Q：评测为什么同时有规则指标和 Judge？

A：规则指标便宜、可复现，适合批量和消融；Judge 更灵活，能评价文本质量和复杂推理。两者互补。

## 14. 证据

- 报告合成：`src/agents/summarizer.py`
- 对抗循环：`src/adversarial/loop.py`
- 对抗数据结构：`src/adversarial/verdict.py`
- Orchestrator 对抗入口：`src/orchestrator/orchestrator.py`
- 规则评测：`evaluation/metrics/rule_based.py`
- 评测脚本：`scripts/run_eval.py`, `scripts/run_ablation.py`, `scripts/run_benchmark.py`
- 配置：`configs/default.yaml`

