# -*- coding: utf-8 -*-
"""6 只核心 ETF 配置（视频备选池）。"""

ETF_POOL = [
    # (代码, 市场, 名称, 核心作用)
    ("510300", "1", "沪深300ETF", "A股大盘核心资产"),
    ("159915", "0", "创业板ETF", "A股高成长赛道"),
    ("513050", "1", "中概互联网ETF", "港股互联网核心龙头"),
    ("513100", "1", "纳指ETF", "海外顶尖科技龙头"),
    ("518880", "1", "黄金ETF", "避险对冲"),
    ("511260", "1", "十年国债ETF", "防守底仓"),
]

NAME_BY_CODE = {c: n for c, _, n, _ in ETF_POOL}
ROLE_BY_CODE = {c: r for c, _, _, r in ETF_POOL}
SECID_BY_CODE = {c: f"{m}.{c}" for c, m, _, _ in ETF_POOL}
