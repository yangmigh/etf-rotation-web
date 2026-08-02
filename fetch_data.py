# -*- coding: utf-8 -*-
"""下载 ETF 日线行情 + 基金净值 到 data/ 目录。"""
import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

from config import ETF_POOL, SECID_BY_CODE

DATA_DIR = Path(__file__).resolve().parent / "data"


def _urlopen(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def fetch_em(secid: str, beg: str, end: str, fqt: int = 1):
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid={secid}&ut=fa5fd1943c7b386f172d6893dbfba10b"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt={fqt}&beg={beg}&end={end}"
    )
    payload = json.loads(_urlopen(url))
    return [",".join(line.split(",")[:6]) for line in payload["data"]["klines"]]


def _tx_window(code: str, beg: str, end: str, mode: str, tries: int = 3):
    """mode: 'qfq'=前复权, 'raw'=不复权(bfq)"""
    market = "sh" if code.startswith("5") else "sz"
    suffix = ",qfq" if mode == "qfq" else ",bfq"
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={market}{code},day,{beg},{end},640{suffix}")
    for i in range(tries):
        try:
            payload = json.loads(_urlopen(url))
            data = payload.get("data")
            if not isinstance(data, dict):
                raise RuntimeError(f"tx bad payload: {str(payload)[:200]}")
            node = data.get(f"{market}{code}")
            if not isinstance(node, dict):
                raise RuntimeError(f"tx missing node {market}{code}: {str(data)[:200]}")
            days = node.get("qfqday") if mode == "qfq" else (node.get("day") or node.get("qfqday"))
            if not days:
                return []
            return [f"{d[0]},{d[1]},{d[2]},{d[3]},{d[4]},{d[5]}" for d in days]
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def fetch_tx(code: str, beg: str, end: str, mode: str = "qfq"):
    rows, seen = [], set()
    y0, y1 = int(beg[:4]), int(end[:4])
    em = end.replace("-", "")
    for y in range(y0, y1 + 1):
        wb = f"{y}-01-01"
        if y < y1:
            we = f"{min(y, y1)}-12-31"
        else:
            we = f"{y1}-{em[4:6]}-{em[6:]}"
        for line in _tx_window(code, wb, we, mode):
            d = line.split(",")[0]
            if d not in seen:
                seen.add(d)
                rows.append(line)
        time.sleep(0.3)
    return rows


def fetch_kline(code: str, secid: str, beg: str = "20200101", end: str = "20260801", tries: int = 5):
    last_err = None
    for i in range(tries):
        try:
            return fetch_em(secid, beg, end, fqt=1)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 + 2 * i)
    try:
        return fetch_tx(code, beg, end, mode="qfq")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"fetch {code} kline failed (em: {last_err}; tx: {e})") from e


def fetch_raw(code: str, secid: str, beg: str = "20200101", end: str = "20260801"):
    """未复权收盘价(date,close)。腾讯bfq优先(稳定), 东财fqt=0备用。"""
    try:
        rows = fetch_tx(code, beg, end, mode="raw")
        if rows:
            return [f"{r.split(',')[0]},{r.split(',')[2]}" for r in rows]
    except Exception as e:  # noqa: BLE001
        last_err = f"tx: {e}"
    try:
        rows = fetch_em(secid, beg, end, fqt=0)
        if rows:
            return [f"{r.split(',')[0]},{r.split(',')[2]}" for r in rows]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"fetch raw {code} failed ({last_err}; em: {e})") from e


def fetch_nav(code: str):
    url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
    txt = _urlopen(url)
    m = re.search(r"Data_netWorthTrend\s*=\s*(\[.*?\]);", txt, re.S)
    if not m:
        raise RuntimeError(f"nav not found for {code}")
    data = json.loads(m.group(1))
    rows = []
    for item in data:
        d = pd_timestamp(item["x"]).date()
        rows.append(f"{d},{item['y']}")
    return rows


def pd_timestamp(ms: int):
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--nav-only", action="store_true")
    args = ap.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    for code, _, name, role in ETF_POOL:
        if not args.nav_only:
            path = DATA_DIR / f"{code}.csv"
            if path.exists() and not args.force:
                print(f"[SKIP] {code} {name} 行情已存在")
            else:
                rows = fetch_kline(code, SECID_BY_CODE[code])
                with open(path, "w", encoding="utf-8") as f:
                    f.write("date,open,close,high,low,volume\n")
                    f.write("\n".join(rows))
                    f.write("\n")
                print(f"[OK] {code} {name} 行情 {len(rows)} 条")
                time.sleep(0.8)
        raw_path = DATA_DIR / f"raw_{code}.csv"
        if raw_path.exists() and not args.force:
            print(f"[SKIP] {code} {name} 原始价已存在")
        else:
            rows = fetch_raw(code, SECID_BY_CODE[code])
            with open(raw_path, "w", encoding="utf-8") as f:
                f.write("date,close\n")
                f.write("\n".join(rows))
                f.write("\n")
            print(f"[OK] {code} {name} 原始价 {len(rows)} 条")
            time.sleep(0.8)
        nav_path = DATA_DIR / f"nav_{code}.csv"
        if nav_path.exists() and not args.force:
            print(f"[SKIP] {code} {name} 净值已存在")
            continue
        rows = fetch_nav(code)
        with open(nav_path, "w", encoding="utf-8") as f:
            f.write("date,nav\n")
            f.write("\n".join(rows))
            f.write("\n")
        print(f"[OK] {code} {name} 净值 {len(rows)} 条")
        time.sleep(0.8)


if __name__ == "__main__":
    main()
