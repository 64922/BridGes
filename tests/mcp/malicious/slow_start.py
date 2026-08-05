"""恶意夹具：启动握手超时（Issue 35 Verification 3 启动超时）。

启动后长时间不输出握手响应，验证宿主在启动超时后停止并回收进程，
状态进入 failed 而非长期 starting。
"""

from __future__ import annotations

import time

time.sleep(30)
