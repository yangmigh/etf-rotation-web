# -*- coding: utf-8 -*-
"""策略核心逻辑: 每日目标持仓。

买入条件（同时满足）:
  1. 近 N 日涨幅在候选 ETF 中排名第一
  2. 收盘价 >= M 日均线 (MA)
卖出/换仓条件（任一触发）:
  1. N 日涨幅跌出第一名 -> 切换到新的第一名
  2. 收盘价跌破 MA -> 空仓
风控: 没有任何标的满足买入条件 -> 空仓
"""
from pathlib import Path

import pandas as pd

from config import ETF_POOL

DEFAULT_RET_WIN = 20
DEFAULT_MA_WIN = 28


def load_close(data_dir: Path, codes=None) -> pd.DataFrame:
    codes = codes or [c for c, *_ in ETF_POOL]
    frames = {}
    for code in codes:
        path = data_dir / f"{code}.csv"
        df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
        frames[code] = df["close"]
    return pd.DataFrame(frames)


def compute_target(close: pd.DataFrame, ret_win: int = DEFAULT_RET_WIN,
                   ma_win: int = DEFAULT_MA_WIN, momentum_positive: bool = False) -> pd.Series:
    """返回每个交易日的目标持仓（ETF 代码或 'CASH'），由当日收盘信号决定，次一交易日生效。"""
    ret_n = close / close.shift(ret_win) - 1.0
    ma = close.rolling(ma_win).mean()

    valid = ret_n.notna().any(axis=1)
    rank1 = pd.Series(index=ret_n.index, dtype=object)
    rank1[valid] = ret_n[valid].idxmax(axis=1, skipna=True)

    above = close >= ma
    ok = pd.Series(index=ret_n.index, dtype=bool)
    for d in ret_n.index[valid]:
        cond = bool(above.loc[d, rank1.loc[d]])
        if momentum_positive:
            cond = cond and bool(ret_n.loc[d, rank1.loc[d]] > 0)
        ok.loc[d] = cond

    target = rank1.where(ok, "CASH").fillna("CASH")
    target.name = "target"
    return target
