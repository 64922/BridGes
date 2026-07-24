# 成熟项目开发路线逻辑原型

这是一次性、无持久化的终端原型，用于回答：

> 阶段门模型能否阻止团队在局部模块“完成”后，绕过跨模块合同、安全硬门、迁移验证、容量与故障演练或四种生产部署等价要求？

它不是生产调度器，不估算工期，也不把任何阶段称为 MVP。正式发布只会在成熟能力基线全部通过后解锁。

在项目根目录、已激活本地 Conda `agent` 开发环境时运行：

```powershell
python .scratch/science-companion-plan/prototypes/development-roadmap/roadmap_tui.py
```

快速运行四个预置难例：

```powershell
python .scratch/science-companion-plan/prototypes/development-roadmap/roadmap_tui.py --demo
```

原型包含：

- [纯状态模型](roadmap_model.py)
- [一次性交互终端](roadmap_tui.py)
- [待确认结论](NOTES.md)

生产实现不得复用这个 TUI。最终只保留经确认的阶段、门禁、责任域和验收结论。

