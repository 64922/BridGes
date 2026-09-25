"""校园通勤模块（V2 Issue 12）：显式选择的日常模块子图。

模块只由随用户消息持久化的 ``module_id=commute`` 或用户点击建议的显式派发
触发，绝不由正文暗中启动（``select_explicit_module`` 只读服务端校验值）。
编排合同为 ``route.parse → route.resolve → route.request → route.buffer →
route.present``；地点坐标、距离、耗时与路径点全部来自高德真实返回，任何
环节不可核验时只展示已证实的最近点与局限，绝不绘制猜测路线。
"""
