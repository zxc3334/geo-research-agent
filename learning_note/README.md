# GeoResearch Agent 一周源码学习文档

这个目录用于帮助你在一周内读懂当前 `deepresearch-agent` 项目，并同步补齐阅读源码需要的 Python 知识。目标不是把 Python 从零学成百科，而是围绕这个项目的真实代码，把“能读懂、能讲清、能改造”作为学习标准。

## 学习目标

一周后你应该能做到：

1. 向面试官讲清 DeepResearch Agent 的完整链路：配置加载、模型路由、规划、DAG 调度、工具调用、记忆、报告合成、对抗修正。
2. 看懂项目中常见 Python 写法：`dataclass`、`Enum`、类型标注、字典/list 结构、异常处理、上下文管理、异步编程、面向对象、工厂函数。
3. 能解释为什么该项目没有用 LangGraph，而是自研 `asyncio + DAG + AgentPool` 调度。
4. 能说出它和主流 DeepResearch 架构的差异，以及你改造成 GeoResearch Agent 的技术路线。
5. 能开始动手新增 GIS/遥感/时空智能相关工具和领域 Planner。

## 文件说明

- [01_week_plan.md](01_week_plan.md)：7 天学习安排，每天读哪些源码、补哪些 Python、产出什么。
- [02_project_architecture.md](02_project_architecture.md)：当前项目总体架构、模块职责、架构图和时序图。
- [03_deepresearch_architectures.md](03_deepresearch_architectures.md)：主流 DeepResearch Agent 设计范式对比。
- [04_python_for_source_reading.md](04_python_for_source_reading.md)：围绕本项目的 Python 重点补课，先 demo 后源码。
- [05_module_deep_dive.md](05_module_deep_dive.md)：逐模块拆解源码，包括入口、Planner、Orchestrator、Agent、Tools、Memory、Adversarial、Evaluation。
- [06_interview_story_and_georesearch_plan.md](06_interview_story_and_georesearch_plan.md)：面试讲法和 GeoResearch Agent 改造路线。
- [07_environment_and_project_foundations.md](07_environment_and_project_foundations.md)：环境配置、依赖管理、`.env`、`pyproject.toml`、`__init__.py`、YAML、日志和项目从零设计理念。

## 建议学习方式

每天按这三个动作循环：

1. **先看 demo**：先理解 Python 语法或设计模式。
2. **再看源码**：打开对应文件，跟着文档定位关键函数。
3. **最后复述**：用自己的话写 5-10 句话，模拟面试讲解。

不要一开始就追求每一行都懂。Agent 项目的重点是数据流和控制流：一个 query 如何变成 sub-task，sub-task 如何触发工具，工具结果如何进入报告。
