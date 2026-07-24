# 领域包专家工作台 UI 原型

这是一次性静态 UI 原型，不是生产代码。

它在一个页面中提供三种结构差异明显的方案，通过 URL 参数和底部切换器切换：

- `A`：阶段控制台；
- `B`：证据与语义 Diff 工作台；
- `C`：责任泳道与失效指挥台。

在项目根目录运行：

```powershell
python -m http.server 8765 --directory .scratch/science-companion-plan/prototypes/domain-pack-expert-workbench
```

然后打开：

- <http://localhost:8765/prototype.html?variant=A>
- <http://localhost:8765/prototype.html?variant=B>
- <http://localhost:8765/prototype.html?variant=C>

请使用页面底部箭头或键盘 `←`、`→` 切换。推荐组合是：A 的主结构、B 的语义 Diff、C 的责任泳道和失效影响带。
