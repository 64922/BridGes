# 系统架构逻辑原型

这是一次性逻辑原型，不是产品代码。

它回答的问题是：设备侧个人保险库、服务器领域模块化单体、持久工作流和四种生产部署，能否在账户切换、分享、撤权、离线编辑、删除与依赖故障下保持相同的授权和状态不变量；同时 Conda `agent` 是否真正只影响本地开发，而不渗入生产合同。

原型使用 Python 标准库，不写数据库、不联网、不读取真实账户或密钥。

在项目根目录运行：

```powershell
python .scratch/science-companion-plan/prototypes/system-architecture/architecture_tui.py
```

如果当前终端已经激活本地开发环境，也可以运行：

```powershell
conda run -n agent python .scratch/science-companion-plan/prototypes/system-architecture/architecture_tui.py
```

第二条命令只是本地开发运行方式，不代表生产需要 Conda。

建议依次尝试：

1. `p`：用户 A 创建私人对象；
2. `s`：复制为项目对象；
3. `u`：切换到用户 B；
4. `r`：尝试读取私人原件；
5. `j`：启动项目任务；
6. `x`：撤销用户 B 的项目权限；
7. `d`：删除项目对象；
8. `o`：模拟旧设备离线编辑；
9. `f`：轮换 Redis、Qwen、沙箱、PostgreSQL 故障；
10. `m`：切换本地开发和四种生产运行方式。

每次操作后都会重绘全部相关状态和最后一次裁决。
