# 🏥 医疗大模型论文日报 (Med-LLM Daily Digest)

每天自动抓取 **arXiv** + **Hugging Face Daily Papers** 中的医疗大模型相关论文，
按主题分类生成 Markdown 日报并自动提交到本仓库。

**关注方向**：医疗 LLM · 医疗 RAG · 医疗 Agent · 评测 / Benchmark · 多模态医疗 · 综述

## 日报在哪里看

- 📂 [`digests/`](digests/) 目录，按日期命名：`digests/2026-11-24.md`
- 每篇论文带：标题链接、日期、来源（arXiv / HuggingFace 热度 👍）、作者、标签、摘要、[alphaXiv AI 解读链接](https://www.alphaxiv.org)
- 同日的 `.json` 文件是结构化数据，方便后续接 LLM 做中文摘要、邮件推送等扩展

## 部署（3 分钟）

```bash
# 1. 在 GitHub 新建一个仓库（比如 med-llm-daily），然后在本目录：
git init
git add .
git commit -m "init: 医疗大模型论文日报"
git remote add origin git@github.com:<你的用户名>/med-llm-daily.git
git push -u origin main
```

2. 打开仓库的 **Actions** 页面，启用 workflow（首次需要点一下确认）。
3. 完成。每天北京时间 08:30 自动运行并提交日报；也可以在 Actions 页面点 **Run workflow** 手动触发。

> 无需任何 API Key、无需安装依赖（纯 Python 标准库）。GitHub 免费额度足够。

## 本地运行

```bash
python3 fetch_papers.py          # 默认抓最近 2 天（周一自动扩到 3 天）
LOOKBACK_DAYS=7 python3 fetch_papers.py   # 抓最近 7 天
```

## 如何调整关注范围

编辑 `fetch_papers.py` 顶部的配置：

| 配置 | 作用 |
|---|---|
| `MEDICAL_TERMS_STRONG` | 医疗强关键词，命中即判定为医疗论文 |
| `MEDICAL_TERMS_WEAK` | 医疗弱关键词（如 diagnostic/health），只用于相关性排序，不单独判定 |
| `TOPIC_TERMS` | 大模型主题词（LLM/RAG/Agent/benchmark…），必须与医疗词同时命中 |
| `TAG_RULES` | 日报分类规则（正则），顺序即分组优先级 |
| `LOOKBACK_DAYS` | 抓取窗口天数（也可用环境变量覆盖） |

## 工作原理

1. **arXiv API**：`医疗词 OR …` AND `主题词 OR …`，按提交时间倒序取 150 篇
2. **本地二次过滤**：arXiv 的 `all:` 匹配是词干化的（会把 "treatment of quantum states" 召回），
   本地用带词边界的正则重新判定「医疗强词 ∩ 主题词」，过滤误报
3. **HF Daily Papers**：取当天热榜，同样过滤；与 arXiv 按论文 ID 去重合并，HF 点赞数用于排序
4. **分类打标**：按 `TAG_RULES` 优先级（评测 > Agent > RAG > 多模态 > 综述）分组输出

## 可选扩展

- **中文 AI 摘要（已内置）**：在仓库 Settings → Secrets and variables → Actions 里加：
  - Secret `LLM_API_KEY`：任意 OpenAI 兼容接口的 key（DeepSeek / 通义 / Kimi / OpenAI…）
  - Variable `LLM_BASE_URL`（可选，默认 `https://api.openai.com/v1`）
  - Variable `LLM_MODEL`（可选，默认 `gpt-4o-mini`）
  配置后日报每篇论文会多一行 💡 中文一句话总结；不配置则自动跳过。
- **邮件 / 飞书 / 微信推送**：在 workflow 里加一步发送 `digests/` 最新文件
- **medRxiv / PubMed 数据源**：在 `fetch_papers.py` 里仿照 `fetch_hf_daily` 增加一个源
