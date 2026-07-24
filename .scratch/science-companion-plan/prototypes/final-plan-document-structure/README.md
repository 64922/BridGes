# 最终规划书结构 UI 原型

这是一次性静态 UI 原型，用于比较最终《科教智能体项目规划书》的三种编排主线，不是生产产品页面。

- `A`：决策与系统蓝图，五部二十二章；
- `B`：科学成长旅程，六条黄金路径；
- `C`：评分证据与验收，要求—章节—证据矩阵。

在项目根目录运行：

```powershell
python -m http.server 8766 --directory .scratch/science-companion-plan/prototypes/final-plan-document-structure
```

然后访问：

- `http://localhost:8766/prototype.html?variant=A`
- `http://localhost:8766/prototype.html?variant=B`
- `http://localhost:8766/prototype.html?variant=C`

可使用底部左右按钮或键盘方向键切换。顶部搜索框只过滤当前方案中的目录或矩阵内容。

## 默认建议

采用 A 作为正文骨架，吸收 B 的黄金路径导航，并把 C 作为第 2 章和附录 B/H/M 的覆盖与验收体系。

