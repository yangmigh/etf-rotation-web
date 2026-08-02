# -*- coding: utf-8 -*-
"""ETF 大类轮动策略 · 网页版 (Streamlit)

本地运行:  streamlit run webapp.py
部署:      见 README-deploy.md (Streamlit Community Cloud / Hugging Face 等)
"""
import datetime as dt
import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import ETF_POOL, NAME_BY_CODE, ROLE_BY_CODE, SECID_BY_CODE
from backtest import run_v2, load_close, load_nav, load_raw, load_fed_regime, DATA_DIR
import fetch_data as fd

POOL4 = ["513100", "159915", "518880", "513050"]
START, END = "2021-03-09", "2026-08-01"
FEE, FREQ = 0.001, "weekly"
RET, MA, PREM = 20, 20, 0.03
FED_EXCLUDE = {"hike": ["159915"]}
IRX_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EIRX?range=10y&interval=1d"

st.set_page_config(page_title="ETF轮动信号", page_icon="📈", layout="wide")


def fetch_irx_to_csv():
    """下载13周美债收益率(^IRX)到 data/us3m_yield.csv。"""
    import json
    import urllib.request
    req = urllib.request.Request(IRX_URL, headers={"User-Agent": "Mozilla/5.0"})
    txt = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "ignore")
    p = json.loads(txt)
    res = p["chart"]["result"][0]
    ts, closes = res["timestamp"], res["indicators"]["quote"][0]["close"]
    rows = []
    for t, c in zip(ts, closes):
        if c is not None:
            d = dt.datetime.fromtimestamp(t).date()
            rows.append(f"{d},{c}")
    with open(DATA_DIR / "us3m_yield.csv", "w", encoding="utf-8") as f:
        f.write("date,yield\n")
        f.write("\n".join(rows))
        f.write("\n")
    return len(rows)


def ensure_data(force: bool = False):
    """确保行情/原始价/净值/美债数据存在且较新。"""
    DATA_DIR.mkdir(exist_ok=True)
    need = force
    today = pd.Timestamp.today().normalize()
    for code, _, _, _ in ETF_POOL:
        for suffix in ["", "raw_", "nav_"]:
            p = DATA_DIR / f"{suffix}{code}.csv"
            if not p.exists():
                need = True
    if not (DATA_DIR / "us3m_yield.csv").exists():
        need = True
    if not need:
        # 行情最新日期距今超过10天 -> 视为需要更新
        try:
            df = pd.read_csv(DATA_DIR / f"{POOL4[0]}.csv", parse_dates=["date"])
            if len(df) and (today - df["date"].max()).days > 10:
                need = True
        except Exception:
            need = True
    if not need:
        return "数据已是最新"
    msgs = []
    with st.spinner("正在联网更新数据(行情/净值/美债)..."):
        for code, _, name, _ in ETF_POOL:
            try:
                rows = fd.fetch_kline(code, SECID_BY_CODE[code])
                (DATA_DIR / f"{code}.csv").write_text("date,open,close,high,low,volume\n" + "\n".join(rows) + "\n", encoding="utf-8")
                rows = fd.fetch_raw(code, SECID_BY_CODE[code])
                (DATA_DIR / f"raw_{code}.csv").write_text("date,close\n" + "\n".join(rows) + "\n", encoding="utf-8")
                rows = fd.fetch_nav(code)
                (DATA_DIR / f"nav_{code}.csv").write_text("date,nav\n" + "\n".join(rows) + "\n", encoding="utf-8")
                msgs.append(f"{code} {name} OK")
            except Exception as e:
                msgs.append(f"{code} 失败: {e}")
            time.sleep(0.5)
        try:
            n = fetch_irx_to_csv()
            msgs.append(f"美债收益率 OK ({n}条)")
        except Exception as e:
            msgs.append(f"美债收益率 失败: {e}")
    return " | ".join(msgs)


# ---------------- 数据 ----------------
with st.sidebar:
    st.header("⚙️ 控制台")
    if st.button("🔄 立即刷新数据"):
        msg = ensure_data(force=True)
        st.success(msg)
        st.rerun()
    st.markdown("**最终配置**")
    st.markdown("- 标的池: 纳指 / 创业板 / 黄金 / 中概\n- 每周调仓(周五收盘)\n- 20日涨幅第一 + MA20\n- 溢价 ≤ 3%\n- 加息期禁买创业板")
    st.markdown("---")
    st.caption("仅供研究参考, 不构成投资建议")

msg = ensure_data(force=False)
st.caption(f"数据状态: {msg}")

try:
    close = load_close(DATA_DIR, POOL4)
    nav_data = load_nav(DATA_DIR, POOL4)
    raw = load_raw(DATA_DIR, POOL4)
    fed = load_fed_regime(close.index)
    irx = pd.read_csv(DATA_DIR / "us3m_yield.csv", parse_dates=["date"]).set_index("date")["yield"].sort_index()
except Exception as e:
    st.error(f"数据加载失败: {e}\n请点击左侧「立即刷新数据」重试。")
    st.stop()

last = close.index[-1]
cur_reg = fed.loc[last]
r = run_v2(close, START, END, FEE, FREQ, RET, MA, premium_th=PREM, nav=nav_data,
           raw_close=raw, fed=fed, fed_exclude=FED_EXCLUDE)

# ---------------- 信号 ----------------
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
        "代码": code, "名称": NAME_BY_CODE[code], "收盘": c, "20日涨幅": r20, f"MA{MA}": m,
        "价>MA": "✅" if c >= m else "❌", "排名": rk, "溢价": p,
        "可买": "❌ 加息期禁买" if fed_block else ("✅" if ok else "❌"),
    })
    if ok and not fed_block:
        cands.append((r20, code))
cands.sort(reverse=True)
rec_code = cands[0][1] if cands else None
rec_name = NAME_BY_CODE[rec_code] if rec_code else "空仓观望"

st.title("📈 ETF 大类轮动策略")
c1, c2, c3, c4 = st.columns(4)
c1.metric("最新交易日", str(last.date()))
c2.metric("操作建议", f"{rec_name}", f"{ret_n.loc[last, rec_code]:+.2%}" if rec_code else "等待机会")
c3.metric("美联储周期", cur_reg.upper(), f"美债 {irx.asof(last):.2f}%")
c4.metric("回测年化", f"{r['annual']:.1%}", f"回撤 {r['max_dd']:.1%}")

st.subheader("📋 信号明细")
df = pd.DataFrame(rows)
df["收盘"] = df["收盘"].map(lambda x: f"{x:.3f}")
df["20日涨幅"] = df["20日涨幅"].map(lambda x: f"{x:+.2%}")
df[f"MA{MA}"] = df[f"MA{MA}"].map(lambda x: f"{x:.3f}")
df["溢价"] = df["溢价"].map(lambda x: f"{x:+.1%}")
st.dataframe(df.set_index("代码"), use_container_width=True)

st.subheader("📊 回测表现 (2021-03-09 ~ 2026-08-01)")
m1, m2, m3, m4 = st.columns(4)
m1.metric("累计收益", f"{r['total']:+.2%}")
m2.metric("年化收益", f"{r['annual']:.2%}")
m3.metric("最大回撤", f"{r['max_dd']:.2%}")
m4.metric("换仓次数", f"{r['n_trades']} 次")

fig = go.Figure()
fig.add_trace(go.Scatter(x=r["nav"].index, y=r["nav"].values, name="策略净值", line=dict(color="#d62728", width=2)))
fig.update_layout(title="净值曲线", xaxis_title="", yaxis_title="净值(起点=1)", height=360,
                  margin=dict(l=10, r=10, t=40, b=10))
st.plotly_chart(fig, use_container_width=True)

fig2 = go.Figure()
fig2.add_trace(go.Bar(x=list(r["yearly"].keys()), y=[v * 100 for v in r["yearly"].values()],
                      marker_color=["#c00" if v < 0 else "#0a0" for v in r["yearly"].values()]))
fig2.update_layout(title="年度收益 (%)", yaxis_title="%", height=300,
                   margin=dict(l=10, r=10, t=40, b=10))
st.plotly_chart(fig2, use_container_width=True)

st.subheader("🕘 最近调仓记录")
st.dataframe(r["trades"].tail(10), use_container_width=True)

st.caption("数据源: 东方财富/腾讯(行情、净值) + Yahoo(美债)。页面数据可点击左侧「立即刷新数据」更新。")
