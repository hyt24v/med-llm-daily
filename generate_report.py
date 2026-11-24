#!/usr/bin/env python3
"""
论文深读报告生成器
==================
读取 digests/YYYY-MM-DD.json, 调用 OpenAI 兼容接口 (默认 Magpie 本地网关)
对每篇论文生成详细中文拆解, 输出自包含 HTML 报告。

用法:
  python3 generate_report.py                       # 处理最新一天的 json
  python3 generate_report.py digests/2026-11-24.json
"""

import glob
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

LLM_API_KEY = os.environ.get("LLM_API_KEY", "magpie-local")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:3425/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen-cn/qwen3.8-flash")

FIELDS = [
    ("background", "🩺 研究背景与问题"),
    ("method", "🧪 方法与创新点"),
    ("data", "📋 数据与实验设置"),
    ("results", "📈 关键结果"),
    ("significance", "💡 意义与启发"),
    ("limitations", "⚠️ 局限与风险"),
]

PROMPT = """你是医疗 AI 领域的资深论文评审。请详细拆解下面这篇论文, 严格输出 JSON (不要输出其他内容):

{{
  "background": "研究背景与要解决的问题, 2-3 句",
  "method": "核心方法与技术路线, 创新点是什么, 3-4 句",
  "data": "用了哪些数据集/基准/模型做实验, 对比了哪些基线, 2-3 句",
  "results": "关键量化结果(保留具体数字), 2-3 句",
  "significance": "这项工作对医疗 LLM/RAG/Agent/评测 领域的意义, 对从业者有什么启发, 2 句",
  "limitations": "局限性与潜在风险, 1-2 句",
  "takeaway": "一句话核心结论 (≤40字)"
}}

要求: 专业、具体、保留数字; 摘要里没说的不要编造, 不确定就写"摘要未提及"。

标题: {title}
摘要: {abstract}"""


def llm_json(prompt: str, max_tokens: int = 1500) -> dict:
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        f"{LLM_BASE_URL}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        text = json.loads(resp.read())["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(m.group(0)) if m else {}


def analyze(p: dict) -> dict:
    try:
        p["analysis"] = llm_json(PROMPT.format(title=p["title"], abstract=p["abstract"][:3000]))
    except Exception as e:
        print(f"[深读] 失败 ({p['id']}): {e}", file=sys.stderr)
        p["analysis"] = {}
    return p


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob("digests/2*.json"))[-1]
    date = os.path.basename(path).replace(".json", "")
    papers = json.load(open(path))
    print(f"共 {len(papers)} 篇, 使用 {LLM_MODEL} @ {LLM_BASE_URL}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(analyze, p): p for p in papers}
        done = 0
        for fut in as_completed(futures):
            fut.result()
            done += 1
            print(f"[深读] {done}/{len(papers)}")

    html = render_html(papers, date)
    out = path.replace(".json", ".html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    # 同时把拆解结果回写 json, 供复用
    with open(path, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)
    print(f"已生成 {out}")


def render_html(papers: list[dict], date: str) -> str:
    tag_color = {
        "📊 评测 / Benchmark": "var(--c1)", "🤖 Agent": "var(--c5)",
        "🔍 RAG": "var(--c2)", "🖼️ 多模态医疗": "var(--c3)",
        "📚 综述 / Survey": "var(--warn)", "🩺 医疗 LLM": "var(--mut)",
    }
    cards = []
    for i, p in enumerate(papers, 1):
        a = p.get("analysis") or {}
        rows = "".join(
            f'<div class="row"><div class="row-label">{label}</div>'
            f'<div class="row-text">{esc(a.get(key, "")) or "<span class=mut>（生成失败）</span>"}</div></div>'
            for key, label in FIELDS if a.get(key) or True
        )
        tags = "".join(
            f'<span class="tag" style="color:{tag_color.get(t, "var(--mut)")};'
            f'border-color:{tag_color.get(t, "var(--mut)")}">{esc(t)}</span>'
            for t in p.get("tags", []))
        votes = f' · 👍 {p["upvotes"]}' if p.get("upvotes") else ""
        takeaway = esc(a.get("takeaway", ""))
        cards.append(f"""
<section class="card" id="p{i}">
  <div class="card-head">
    <span class="no">#{i:02d}</span>
    <span class="tags">{tags}</span>
  </div>
  <h2><a href="{esc(p['url'])}">{esc(p['title'])}</a></h2>
  <div class="meta">{esc(p['published'])} · {esc(p['source'])}{votes} · {esc(', '.join(p['authors'][:3]))}</div>
  {f'<div class="takeaway">💊 {takeaway}</div>' if takeaway else ''}
  <div class="rows">{rows}</div>
  <div class="links"><a href="{esc(p['url'])}">arXiv 原文</a> · <a href="https://www.alphaxiv.org/abs/{esc(p['id'])}">alphaXiv 讨论</a></div>
</section>""")

    toc = "".join(
        f'<a href="#p{i}" class="toc-item"><span class="toc-no">{i:02d}</span>{esc(p["title"][:52])}</a>'
        for i, p in enumerate(papers, 1))

    tag_counts: dict[str, int] = {}
    for p in papers:
        t = p.get("primary_tag", "")
        tag_counts[t] = tag_counts.get(t, 0) + 1
    stats = "".join(
        f'<div class="stat"><div class="stat-num" style="color:{tag_color.get(k, "var(--mut)")}">{v}</div>'
        f'<div class="stat-label">{esc(k)}</div></div>'
        for k, v in tag_counts.items())

    return TEMPLATE.format(date=date, n=len(papers), stats=stats, toc=toc,
                           cards="\n".join(cards), model=esc(LLM_MODEL))


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>医疗大模型论文深读 · {date}</title>
<style>
/* 1. fallback palette (light + dark) */
:root {{
  --fb-fg:#1c1f24; --fb-bg:#ffffff; --fb-card:#f5f6f8; --fb-bd:#dfe2e8; --fb-mut:#5c626b;
  --fb-c1:#3f6fe0; --fb-c2:#1d9e6a; --fb-c3:#d9931e; --fb-c5:#8a63e8;
  --fb-red:#d5433c; --fb-pri:#2f5fd0; --fb-rd:10px;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --fb-fg:#e9ebef; --fb-bg:#141619; --fb-card:#1e2126; --fb-bd:#363a42; --fb-mut:#9aa0aa;
    --fb-c1:#7aa2f0; --fb-c2:#3ec98d; --fb-c3:#e0aa45; --fb-c5:#a78bfa;
    --fb-red:#e0605a; --fb-pri:#7aa2f0;
  }}
}}
/* 2. aliases: host theme var wins when present, fallback otherwise */
:root {{
  --fg: var(--foreground, var(--fb-fg));
  --bg: var(--background, var(--fb-bg));
  --card-bg: var(--card, var(--fb-card));
  --bd: var(--border, var(--fb-bd));
  --mut: var(--muted-foreground, var(--fb-mut));
  --c1: var(--chart-1, var(--fb-c1));
  --c2: var(--chart-2, var(--fb-c2));
  --c3: var(--chart-3, var(--fb-c3));
  --c5: var(--chart-5, var(--fb-c5));
  --red: var(--destructive, var(--fb-red));
  --ok: var(--success, var(--c2));
  --warn: var(--warning, var(--c3));
  --pri: var(--primary, var(--fb-pri));
  --rd: var(--radius, var(--fb-rd));
  --fsans: var(--font-sans, -apple-system, "PingFang SC", "Helvetica Neue", sans-serif);
  --fmono: var(--font-mono, ui-monospace, "SF Mono", Menlo, monospace);
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: var(--fsans); color: var(--fg); line-height:1.65; }}
a {{ color: var(--pri); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.wrap {{ max-width: 760px; margin: 0 auto; padding: 8px 4px 48px; }}

header {{ padding: 20px 16px 16px; border:1px solid var(--bd); border-radius: var(--rd); background: var(--card-bg); position: relative; overflow: hidden; }}
header::before {{ content:""; position:absolute; top:0; left:0; right:0; height:3px;
  background: linear-gradient(90deg, var(--c1), var(--c2), var(--c3), var(--c5)); }}
.kicker {{ font-family: var(--fmono); font-size:12px; color: var(--mut); letter-spacing:1px; }}
h1 {{ font-size: 22px; margin: 6px 0 4px; }}
.subtitle {{ color: var(--mut); font-size: 13px; }}
.stats {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:14px; }}
.stat {{ flex:1; min-width:96px; border:1px solid var(--bd); border-radius: var(--rd); padding:8px 10px; }}
.stat-num {{ font-size:22px; font-weight:700; font-family: var(--fmono); }}
.stat-label {{ font-size:11px; color: var(--mut); }}

.toc {{ margin: 20px 0; padding: 14px 16px; border:1px dashed var(--bd); border-radius: var(--rd); }}
.toc-title {{ font-size:13px; color: var(--mut); margin-bottom:8px; font-family: var(--fmono); }}
.toc-item {{ display:block; padding:3px 0; font-size:13px; color: var(--fg); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.toc-no {{ font-family: var(--fmono); color: var(--mut); margin-right:8px; }}

.card {{ border:1px solid var(--bd); border-radius: var(--rd); background: var(--card-bg); padding:16px; margin:16px 0; }}
.card-head {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
.no {{ font-family: var(--fmono); font-weight:700; color: var(--mut); font-size:14px; }}
.tag {{ font-size:11px; border:1px solid; border-radius:99px; padding:1px 8px; margin-right:4px; white-space:nowrap; }}
.card h2 {{ font-size:17px; margin:8px 0 4px; line-height:1.4; }}
.meta {{ font-size:12px; color: var(--mut); margin-bottom:10px; }}
.takeaway {{ border-left:3px solid var(--c2); padding:6px 12px; margin:10px 0; font-size:14px; font-weight:600; background: color-mix(in srgb, var(--c2) 8%, transparent); border-radius:0 var(--rd) var(--rd) 0; }}
.rows {{ margin-top:8px; }}
.row {{ display:flex; gap:12px; padding:8px 0; border-top:1px solid var(--bd); font-size:13.5px; }}
.row-label {{ flex:0 0 128px; font-weight:600; color: var(--mut); }}
.row-text {{ flex:1; }}
.links {{ margin-top:10px; font-size:12px; font-family: var(--fmono); }}
.mut {{ color: var(--mut); }}
footer {{ margin-top:28px; font-size:12px; color: var(--mut); text-align:center; font-family: var(--fmono); }}
@media (max-width:480px) {{ .row {{ flex-direction:column; gap:2px; }} .row-label {{ flex:none; }} }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="kicker">MED-LLM DEEP READING REPORT</div>
  <h1>🏥 医疗大模型论文深读 · {date}</h1>
  <div class="subtitle">数据源 arXiv + Hugging Face Daily Papers · 由 {model} 生成拆解 · 关注 医疗LLM / RAG / Agent / 评测Benchmark</div>
  <div class="stats">
    <div class="stat"><div class="stat-num">{n}</div><div class="stat-label">论文总数</div></div>
    {stats}
  </div>
</header>

<nav class="toc">
  <div class="toc-title">📑 目录 (点击跳转)</div>
  {toc}
</nav>

{cards}

<footer>generated by med-llm-daily · {date} · model: {model}</footer>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    main()
