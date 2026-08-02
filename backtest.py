# -*- coding: utf-8 -*-
"""回测: ETF 大类轮动策略（含三项改进）。

基础规则:
  1. 近 N 日涨幅在池内排名最前的标的（价格须站上均线）才可买入
  2. 排名/均线失效则换仓或空仓

三项改进（默认关闭, 用参数开启）:
  --top2     持仓前2名等权(各50%), 分散单标的暴雷风险
  --stop 0.20  硬止损: 任一持仓自买入价回撤达20%即无条件卖出
  --premium 0.03 溢价过滤: 场内价相对基金净值溢价超过阈值(如3%)的标的不可买入

用法:
  python backtest.py --codes 513100,159915,518880,513050 --freq weekly --ret 20 --ma 20
  python backtest.py --codes ... --top2 --stop 0.2 --premium 0.03   # 三项改进全开
"""
import argparse
from pathlib import Path

import pandas as pd

from config import ETF_POOL, NAME_BY_CODE
from strategy import load_close

DATA_DIR = Path(__file__).resolve().parent / "data"
OUT_DIR = Path(__file__).resolve().parent / "output"
DEFAULT_FEE = 0.001
TRADING_DAYS = 252




def load_fed_regime(index=None, months: int = 12, threshold: float = 0.25) -> pd.Series:
    """13周美债收益率 -> 政策周期。加息: 较months个月前上升>threshold; 降息: 下降>threshold; 否则中性。"""
    path = Path(__file__).resolve().parent / "data" / "us3m_yield.csv"
    irx = pd.read_csv(path, parse_dates=["date"]).set_index("date")["yield"].sort_index()
    if index is not None:
        irx = irx.reindex(index, method="ffill")
    chg = irx - irx.shift(int(21 * months))
    regime = pd.Series("neutral", index=irx.index)
    regime[chg > threshold] = "hike"
    regime[chg < -threshold] = "cut"
    return regime


def load_raw(data_dir: Path, codes) -> pd.DataFrame:
    """未复权收盘价(用于计算场内溢价)。"""
    frames = {}
    for code in codes:
        path = data_dir / f"raw_{code}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
        frames[code] = df["close"]
    if not frames:
        return pd.DataFrame()
    return pd.DataFrame(frames)

def load_nav(data_dir: Path, codes) -> pd.DataFrame:
    frames = {}
    for code in codes:
        path = data_dir / f"nav_{code}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
        frames[code] = df["nav"]
    if not frames:
        return pd.DataFrame()
    return pd.DataFrame(frames)


def run_v2(close: pd.DataFrame, start: str, end: str, fee: float = DEFAULT_FEE,
           freq: str = "weekly", ret_win: int = 20, ma_win: int = 20,
           top2: bool = False, stop: float = None, premium_th: float = None,
           nav: pd.DataFrame = None, raw_close: pd.DataFrame = None,
           fed: pd.Series = None, fed_exclude: dict = None):
    """事件驱动回测引擎。close: 收盘价; nav: 净值(可选, 用于溢价过滤)。"""
    ret = close.pct_change(fill_method=None)
    ret_n = close / close.shift(ret_win) - 1.0
    ma = close.rolling(ma_win).mean()

    # 溢价序列: 场内价 / 最近净值 - 1
    ok_prem = None
    if premium_th is not None and nav is not None and len(nav):
        prem = pd.DataFrame(0.0, index=close.index, columns=close.columns)
        px = raw_close if raw_close is not None and len(raw_close) else close
        for code in close.columns:
            nv = nav[code].dropna() if code in nav.columns else pd.Series(dtype=float)
            if len(nv) and code in px.columns:
                aligned = nv.reindex(close.index, method="ffill")
                prem[code] = (px[code] / aligned - 1.0).fillna(0.0)
        ok_prem = prem <= premium_th

    ok_ma = close >= ma

    if freq == "daily":
        rebal_days = set(ret.index)
    else:
        per = ret.index.to_period("M" if freq == "monthly" else "W")
        rebal_days = set(ret.index.to_series().groupby(per).max())

    positions = {}   # code -> weight
    entries = {}     # code -> 入场价
    strat = pd.Series(0.0, index=ret.index, dtype=float)
    held = {}        # date -> bool(在市场中)
    trades = []

    for d in ret.index:
        held[d] = bool(positions)

        # 当日收益(close-to-close)
        day_ret = 0.0
        for code, w in positions.items():
            r = ret.at[d, code]
            if pd.notna(r):
                day_ret += w * r
        strat.at[d] = day_ret

        # 硬止损(每日检查, 与调仓日无关)
        if stop:
            for code in list(positions):
                if close.at[d, code] / entries[code] - 1.0 <= -stop:
                    w = positions.pop(code)
                    del entries[code]
                    strat.at[d] -= fee / 2 * w
                    trades.append((d, NAME_BY_CODE.get(code, code), "空仓(止损)"))

        # 调仓(仅调仓日)
        if d in rebal_days:
            quals = []
            reg_now = None
            if fed is not None:
                reg_now = fed.get(d) if hasattr(fed, "get") else None
                if reg_now is None:
                    reg_now = fed.loc[d] if d in fed.index else None
            for code in close.columns:
                if not bool(ok_ma.at[d, code]):
                    continue
                if ok_prem is not None and not bool(ok_prem.at[d, code]):
                    continue
                if fed_exclude and reg_now in fed_exclude and code in fed_exclude[reg_now]:
                    continue
                r20 = ret_n.at[d, code]
                if pd.notna(r20):
                    quals.append((r20, code))
            quals.sort(reverse=True)

            if top2:
                chosen = [(quals[0][1], 0.5), (quals[1][1], 0.5)] if len(quals) >= 2 else \
                         ([(quals[0][1], 1.0)] if quals else [])
            else:
                chosen = [(quals[0][1], 1.0)] if quals else []
            new_pos = dict(chosen)

            # 换仓成本: 每边费 = fee/2 * 权重变动量
            cost, sold, bought = 0.0, [], []
            for code in set(positions) | set(new_pos):
                old_w = positions.get(code, 0.0)
                new_w = new_pos.get(code, 0.0)
                if new_w > old_w:
                    cost += fee / 2 * (new_w - old_w)
                    bought.append(NAME_BY_CODE.get(code, code))
                elif new_w < old_w:
                    cost += fee / 2 * (old_w - new_w)
                    sold.append(NAME_BY_CODE.get(code, code))
            if cost > 0:
                strat.at[d] -= cost
                trades.append((d, "+".join(sold) if sold else "空仓",
                               "+".join(bought) if bought else "空仓"))

            for code, w in new_pos.items():
                if code not in entries:
                    entries[code] = close.at[d, code]
            positions = new_pos

    # ---------- 汇总 ----------
    mask = (strat.index >= pd.Timestamp(start)) & (strat.index <= pd.Timestamp(end))
    strat_w = strat[mask].iloc[1:]
    nav_s = (1.0 + strat_w).cumprod()
    dd = nav_s / nav_s.cummax() - 1.0
    total = float(nav_s.iloc[-1] - 1.0)
    years = len(nav_s) / TRADING_DAYS
    annual = float(nav_s.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else float("nan")
    max_dd = float(dd.min())
    win_days = float((strat_w > 0).mean())

    yearly = {}
    for year in sorted(set(nav_s.index.year)):
        y_nav = nav_s[nav_s.index.year == year]
        if len(y_nav) == 0:
            continue
        prev = nav_s[nav_s.index < y_nav.index[0]]
        base = float(prev.iloc[-1]) if len(prev) else 1.0
        yearly[str(year)] = float(y_nav.iloc[-1] / base - 1.0)

    trades_df = pd.DataFrame(
        [(d.date(), s, b) for d, s, b in trades if d >= pd.Timestamp(start)],
        columns=["日期", "卖出", "买入"],
    )

    bench = {}
    for code in close.columns:
        s = close[code][(close.index >= pd.Timestamp(start)) & (close.index <= pd.Timestamp(end))]
        if len(s):
            bench[NAME_BY_CODE.get(code, code)] = float(s.iloc[-1] / s.iloc[0] - 1.0)

    in_market = float(pd.Series(held)[strat_w.index].mean())

    return {
        "nav": nav_s, "strat": strat_w, "dd": dd, "total": total, "annual": annual,
        "max_dd": max_dd, "win_days": win_days, "yearly": yearly,
        "trades": trades_df, "bench": bench,
        "n_trades": len(trades_df), "in_market": in_market,
    }


def run(close, start, end, fee=DEFAULT_FEE, freq="weekly", ret_win=20, ma_win=28, momentum=False):
    """向后兼容的旧接口(无三项改进)。"""
    return run_v2(close, start, end, fee, freq, ret_win, ma_win, top2=False, stop=None,
                  premium_th=None, nav=None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-03-09")
    ap.add_argument("--end", default="2026-08-01")
    ap.add_argument("--fee", type=float, default=DEFAULT_FEE)
    ap.add_argument("--freq", choices=["daily", "weekly", "monthly"], default="weekly")
    ap.add_argument("--codes", default="", help="逗号分隔的ETF代码子集, 默认全部6只")
    ap.add_argument("--ret", type=int, default=20)
    ap.add_argument("--ma", type=int, default=20)
    ap.add_argument("--top2", action="store_true", help="持仓前2名等权(各50%)")
    ap.add_argument("--stop", nargs="?", const=0.2, type=float, default=None,
                    help="硬止损比例, 如 --stop 0.2 表示自买入价回撤20%无条件卖出")
    ap.add_argument("--premium", type=float, default=None,
                    help="溢价阈值, 如 --premium 0.03 表示场内价相对净值溢价超3%不可买入")
    ap.add_argument("--fed", choices=["none", "hike_no_cyb", "hike_no_growth"], default="none",
                    help="美联储政策周期过滤: hike_no_cyb=加息期禁买创业板(推荐); hike_no_growth=加息期禁买创业板+纳指")
    args = ap.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or [c for c, *_ in ETF_POOL]
    close = load_close(DATA_DIR, codes)
    nav = load_nav(DATA_DIR, codes) if args.premium is not None else None
    raw_close = load_raw(DATA_DIR, codes) if args.premium is not None else None
    if args.premium is not None and (nav.empty or raw_close.empty):
        print("[错误] 未找到净值/原始价数据, 请先运行: python fetch_data.py")
        return

    fed_exclude = None
    fed_regime = None
    if args.fed != "none":
        fed_regime = load_fed_regime(close.index)
        fed_exclude = {"hike": ["159915"]} if args.fed == "hike_no_cyb" else {"hike": ["159915", "513100"]}
        cur = fed_regime.loc[close.index[-1]]
        label = "禁买创业板" if args.fed == "hike_no_cyb" else "禁买创业板+纳指"
        print(f"[Fed过滤] 当前政策周期: {cur}   (加息期{label})")

    res = run_v2(close, args.start, args.end, args.fee, args.freq, args.ret, args.ma,
                 top2=args.top2, stop=args.stop, premium_th=args.premium, nav=nav,
                 raw_close=raw_close, fed=fed_regime, fed_exclude=fed_exclude)
    OUT_DIR.mkdir(exist_ok=True)

    tag = "_".join(close.columns) + f"_{args.freq}_r{args.ret}_m{args.ma}" + \
          ("_top2" if args.top2 else "") + (f"_stop{args.stop:.0%}" if args.stop else "") + \
          (f"_prem{args.premium:.0%}" if args.premium is not None else "")
    eq = pd.DataFrame({"nav": res["nav"], "drawdown": res["dd"], "daily_ret": res["strat"]})
    eq.to_csv(OUT_DIR / f"equity_curve_{tag}.csv", encoding="utf-8-sig")
    res["trades"].to_csv(OUT_DIR / f"trades_{tag}.csv", index=False, encoding="utf-8-sig")

    line = "=" * 66
    print(line)
    print(f"标的池: {', '.join(NAME_BY_CODE.get(c, c) for c in close.columns)}")
    print(f"区间: {args.start} ~ {args.end}  频率: {args.freq}  参数: {args.ret}日/MA{args.ma}  "
          f"top2={args.top2}  stop={args.stop}  premium={args.premium}")
    print(f"累计收益: {res['total']:.2%}   年化: {res['annual']:.2%}   最大回撤: {res['max_dd']:.2%}")
    print(f"换仓次数: {res['n_trades']}   在场时间: {res['in_market']:.1%}   盈利天数: {res['win_days']:.1%}")
    print(line)
    print("年度收益:", "  ".join(f"{y}{v:+.1%}" for y, v in res["yearly"].items()))
    print(line)
    print(f"输出 -> {OUT_DIR / f'equity_curve_{tag}.csv'} / trades_{tag}.csv")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
        ax[0].plot(res["nav"].index, res["nav"].values, lw=1.6, color="#d62728", label="改进后策略")
        ax[0].set_title("ETF 轮动策略(改进版) 净值")
        ax[0].legend(); ax[0].grid(alpha=0.3)
        ax[1].fill_between(res["dd"].index, res["dd"].values * 100, 0, color="#888888", alpha=0.5)
        ax[1].set_ylabel("回撤 %"); ax[1].grid(alpha=0.3)
        chart = OUT_DIR / f"equity_curve_{tag}.png"
        fig.tight_layout(); fig.savefig(chart, dpi=130)
        print(f"图表 -> {chart}")
    except Exception as e:  # noqa: BLE001
        print(f"(图表生成跳过: {e})")


if __name__ == "__main__":
    main()
