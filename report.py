# -*- coding: utf-8 -*-
"""生成每周信号报告: HTML(内嵌图表) + PNG + TXT, 按日期存档到 reports/。

用法:
  python report.py                 # 用最新交易日生成报告
  python report.py --date 2026-07-31   # 指定日期(复盘用)
"""
import argparse
import base64
import io
from pathlib import Path

import pandas as pd

from backtest import run_v2, load_close, load_nav, load_raw, load_fed_regime, DATA_DIR
from config import NAME_BY_CODE, ROLE_BY_CODE

POOL4 = ["513100", "159915", "518880", "513050"]
START, END = "2021-03-09", "2026-08-01"
FEE, FREQ = 0.001, "weekly"
RET, MA, PREM = 20, 20, 0.03
FED_EXCLUDE = {"hike": ["159915"]}
OUT_DIR = Path(__file__).resolve().parent / "reports"


def make_chart(nav, dd, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                           gridspec_kw={"height_ratios": [3, 1]})
    ax[0].plot(nav.index, nav.values, lw=1.6, color="#d62728")
    ax[0].set_ylabel("净值(起点=1)")
    ax[0].set_title("ETF轮动策略(最终配置) 净值曲线")
    ax[0].grid(alpha=0.3)
    ax[1].fill_between(dd.index, dd.values * 100, 0, color="#888888", alpha=0.5)
    ax[1].set_ylabel("回撤 %")
    ax[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="复盘指定日期(YYYY-MM-DD), 默认最新交易日")
    args = ap.parse_args()

    close = load_close(DATA_DIR, POOL4)
    nav_data = load_nav(DATA_DIR, POOL4)
    raw = load_raw(DATA_DIR, POOL4)
    fed = load_fed_regime(close.index)
    irx = pd.read_csv("data/us3m_yield.csv", parse_dates=["date"]).set_index("date")["yield"].sort_index()

    last = close.index[-1] if not args.date else pd.Timestamp(args.date)
    if last not in close.index:
        last = close.index.asof(last)
    cur_reg = fed.loc[last]

    r = run_v2(close, START, END, FEE, FREQ, RET, MA, premium_th=PREM, nav=nav_data,
               raw_close=raw, fed=fed, fed_exclude=FED_EXCLUDE)

    # ---- 信号表 ----
    ret_n = close / close.shift(RET) - 1.0
    ma = close.rolling(MA).mean()
    rank = ret_n.rank(axis=1, ascending=False).fillna(0).astype(int)
    prem = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for code in POOL4:
        nv = nav_data[code].dropna()
        aligned = nv.reindex(raw.index, method="ffill")
        prem[code] = (raw[code] / aligned - 1.0).fillna(0.0)

    rows = []
    cands = []
    for code in POOL4:
        c = close.loc[last, code]
        r20 = ret_n.loc[last, code]
        m = ma.loc[last, code]
        rk = int(rank.loc[last, code])
        p = prem.loc[last, code]
        ok = (c >= m) and (p <= PREM)
        fed_block = (cur_reg == "hike" and code in FED_EXCLUDE.get("hike", []))
        rows.append({
            "代码": code, "名称": NAME_BY_CODE[code], "收盘": c, "20日涨幅": r20,
            f"MA{MA}": m, "价>MA": "是" if c >= m else "否", "排名": rk,
            "溢价": p, "可买": "否(Fed)" if fed_block else ("是" if ok else "否"),
        })
        if ok and not fed_block:
            cands.append((r20, code))
    cands.sort(reverse=True)
    rec = NAME_BY_CODE[cands[0][1]] + f" ({cands[0][1]})" if cands else "空仓观望"

    # ---- 最近交易 ----
    recent = r["trades"].tail(8)

    # ---- 图表 ----
    OUT_DIR.mkdir(exist_ok=True)
    chart_path = OUT_DIR / f"chart_{last.date()}.png"
    make_chart(r["nav"], r["dd"], chart_path)
    b64 = base64.b64encode(chart_path.read_bytes()).decode()

    # ---- HTML ----
    trs = "".join(
        f"<tr><td>{x['代码']}</td><td>{x['名称']}</td><td>{x['收盘']:.3f}</td>"
        f"<td>{x['20日涨幅']:+.2%}</td><td>{x[f'MA{MA}']:.3f}</td><td>{x['价>MA']}</td>"
        f"<td>{x['排名']}</td><td>{x['溢价']:+.1%}</td><td class='{'ok' if x['可买']=='是' else 'no'}'>{x['可买']}</td></tr>"
        for x in rows)
    yearly = "".join(f"<td>{y} {v:+.1%}</td>" for y, v in r["yearly"].items())
    trades_rows = "".join(
        f"<tr><td>{t.日期}</td><td>{t.卖出}</td><td>{t.买入}</td></tr>"
        for t in recent.itertuples(index=False))

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>ETF轮动信号报告 {last.date()}</title>
<style>
body{{font-family:'Microsoft YaHei',sans-serif;margin:24px;color:#222}}
h1{{font-size:20px}} h2{{font-size:16px;margin-top:28px;border-left:4px solid #d62728;padding-left:8px}}
table{{border-collapse:collapse;font-size:13px}} td,th{{border:1px solid #ccc;padding:6px 10px;text-align:center}}
th{{background:#f5f5f5}} .ok{{color:#0a0;font-weight:bold}} .no{{color:#c00}}
.summary td{{background:#fafafa}} img{{max-width:100%}}
</style></head><body>
<h1>📈 ETF 大类轮动 · 每周信号报告</h1>
<p>报告日期: <b>{last.date()}</b> ｜ 美联储政策周期: <b>{cur_reg}</b> ｜ 13周美债: {irx.asof(last):.2f}%
<br>配置: 4只池子 + 每周调仓 + 20日涨幅/MA20 + 溢价≤3% + 加息期禁买创业板</p>

<h2>最新操作建议</h2>
<p style="font-size:18px"><b>持有/买入 {rec}</b></p>
<table class="summary"><tr><th>累计收益(回测)</th><th>年化</th><th>最大回撤</th><th>换仓</th><th>在场时间</th></tr>
<tr><td>{r['total']:+.2%}</td><td>{r['annual']:+.2%}</td><td>{r['max_dd']:.2%}</td><td>{r['n_trades']}次</td><td>{r['in_market']:.1%}</td></tr></table>
<table><tr><th>年度收益</th>{yearly}</tr></table>

<h2>信号明细（{last.date()}）</h2>
<table><tr><th>代码</th><th>名称</th><th>收盘</th><th>20日涨幅</th><th>MA{MA}</th><th>价>MA</th><th>排名</th><th>溢价</th><th>可买?</th></tr>{trs}</table>

<h2>净值与回撤</h2>
<img src="data:image/png;base64,{b64}" alt="净值曲线">

<h2>最近调仓记录</h2>
<table><tr><th>日期</th><th>卖出</th><th>买入</th></tr>{trades_rows}</table>

<p style="color:#888;font-size:12px;margin-top:30px">本报告由 etf_rotation/report.py 自动生成，仅供研究参考，不构成投资建议。
每周五收盘后运行: python report.py</p>
</body></html>"""

    out_html = OUT_DIR / f"signal_report_{last.date()}.html"
    out_html.write_text(html, encoding="utf-8")
    (OUT_DIR / "index.html").write_text(html, encoding="utf-8")   # 最新报告作为站点首页

    # ---- TXT ----
    lines = []
    lines.append("=" * 62)
    lines.append(f"ETF轮动信号报告  {last.date()}   政策周期: {cur_reg}   13周美债: {irx.asof(last):.2f}%")
    lines.append(f"操作建议: 持有/买入 {rec}")
    lines.append(f"回测({START}~{END}): 累计{r['total']:+.2%} 年化{r['annual']:+.2%} 回撤{r['max_dd']:.2%}")
    lines.append("年度: " + "  ".join(f"{y}{v:+.1%}" for y, v in r["yearly"].items()))
    lines.append("-" * 62)
    lines.append(f"{'代码':<8}{'名称':<13}{'收盘':>8}{'20日涨幅':>9}{'MA20':>8}{'排名':>5}{'溢价':>7}{'可买':>7}")
    for x in rows:
        lines.append(f"{x['代码']:<8}{x['名称']:<13}{x['收盘']:>8.3f}{x['20日涨幅']:>8.2%}{x[f'MA{MA}']:>8.3f}{x['排名']:>5}{x['溢价']:>+6.1%}{x['可买']:>9}")
    lines.append("-" * 62)
    for t in recent.itertuples(index=False):
        lines.append(f"  {t.日期}  卖出: {t.卖出}  买入: {t.买入}")
    lines.append("=" * 62)
    out_txt = OUT_DIR / f"signal_report_{last.date()}.txt"
    out_txt.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines[:10]))
    print(f"\n[生成] {out_html}")
    print(f"[生成] {out_txt}")
    print(f"[生成] {chart_path}")


if __name__ == "__main__":
    main()
