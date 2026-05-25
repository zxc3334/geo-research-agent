# DeepResearch Agent 学习包

这套文档按“学生第一次准备实习面试”的顺序写，不要求你一开始就懂 Agent 框架、异步、RAG、评测。

## 推荐学习顺序

| 阶段 | 文档 | 目标 |
|---|---|---|
| 0 | `00-project-anatomy-and-language-primer.md` | 先看懂 Python 项目骨架、依赖、`.env`、YAML、异步语法 |
| 1 | `01-architecture-overview.md` | 建立整体架构地图 |
| 2 | `modules/02-cli-runner-config.md` | 看懂命令行入口、配置加载、模块初始化 |
| 3 | `modules/03-planner-dag-orchestrator.md` | 看懂 Planner、DAG、状态机和异步并发调度 |
| 4 | `modules/04-agent-tool-calling-and-tools.md` | 看懂 ResearcherAgent 和工具调用 |
| 5 | `modules/05-model-router-memory-compressor.md` | 看懂模型路由、记忆和压缩 |
| 6 | `modules/06-summarizer-adversarial-evaluation.md` | 看懂报告合成、Red-Blue 修正和评测 |
| 7 | `99-interview-questions.md` | 转换成面试表达和简历 bullet |

## 每阶段检查

学完每一阶段后，你至少能回答三类问题：

1. 这个模块输入是什么？
2. 这个模块输出是什么？
3. 如果它失败，系统怎么降级？

## 面试主线

月底前优先讲熟这条链路：

```text
run_single.py
  -> runner.load_config / initialize_modules
  -> Orchestrator.run
  -> Planner.generate_plan
  -> DAG.get_parallel_groups
  -> ResearcherAgent.run
  -> tool.execute
  -> SummarizerAgent.run
  -> AdversarialLoop.run
  -> save_report
```

不要一开始主讲 Evolution/GRPO。它更像扩展模块，等主链路稳了再学。

