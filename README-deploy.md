# 免费部署指南：ETF 轮动策略网页版

网页应用文件：`webapp.py`（Streamlit）。支持免费部署到以下平台。

## 方式一：Streamlit Community Cloud（推荐，永久免费）

1. **注册 GitHub**（如果没有）：https://github.com 免费注册
2. **新建仓库**：GitHub → New repository → 名字如 `etf-rotation-web`，勾选 Private（或 Public 均可）
3. **上传代码**：把下面这些文件上传到仓库根目录（用网页上传或 git push）：
   ```
   webapp.py
   fetch_data.py
   fetch_irx.py       (如没有可省略，webapp 内置了美债下载)
   config.py
   strategy.py
   backtest.py
   requirements.txt
   .streamlit/config.toml
   ```
   > 注意：`data/` 目录可以不传（应用启动时会自动联网下载数据）；
   > `output/`、`reports/`、`patch_*.py`、`improve*.py` 等本地脚本无需上传。
4. **连接部署**：打开 https://share.streamlit.io → Sign in with GitHub → New app
   → 选择刚建的仓库，Main file 填 `webapp.py` → Deploy
5. **完成**：几分钟后得到一个 `https://xxx.streamlit.app` 网址，手机电脑都能打开。

后续更新数据：打开网页 → 点左侧「🔄 立即刷新数据」按钮即可（免费额度内每天运行没问题）。

## 方式二：Hugging Face Spaces（免费）

1. https://huggingface.co 注册 → New Space → SDK 选 **Streamlit**
2. 上传同样文件，`requirements.txt` 不变
3. 得到 `https://huggingface.co/spaces/你的名字/etf-rotation`，可直接用

## 方式三：先在本机看效果

```bash
cd etf_rotation
streamlit run webapp.py
```
浏览器自动打开 http://localhost:8501

## 注意事项

- 免费平台首次启动会自动联网下载行情/净值/美债数据（约 30~60 秒），之后打开很快；
- 数据每天可手动刷新；若要完全自动化，建议配合 GitHub Actions 或平台定时任务，
  或继续用本机 `report.py` + Windows 计划任务（周五自动生成报告）；
- 页面仅供个人研究，请勿用于投资建议或商业用途。
