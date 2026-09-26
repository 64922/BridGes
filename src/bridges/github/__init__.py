"""GitHub 项目推荐模块（V2 Issue 16）。

包初始化刻意不导入编排服务：``bridges.contracts.chat`` 会导入
``bridges.github.contracts``，若在此处导入 ``bridges.github.service`` 就会
形成包级循环。调用方按需导入 ``bridges.github.service``。
"""
