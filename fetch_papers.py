#!/usr/bin/env python3
"""
医疗大模型论文日报抓取器
========================
数据源:
  1. arXiv API  —— 医疗关键词 AND 大模型主题关键词, 按提交时间倒序
  2. Hugging Face Daily Papers —— 取社区热度, 按医疗关键词过滤

输出:
  digests/YYYY-MM-DD.md   分类日报 (评测Benchmark / Agent / RAG / 医疗LLM / 多模态 / 综述)
  digests/YYYY-MM-DD.json 结构化数据 (供后续接 LLM 摘要等扩展)

纯标准库实现, 无需 pip install。
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------- 配置

# 医疗强关键词 (命中其一即判定为医疗论文)
MEDICAL_TERMS_STRONG = [
    "medical", "medicine", "clinical", "clinician", "biomedical", "biomedicine",
    "healthcare", "patient", "disease", "hospital", "radiology", "radiologist",
    "pathology", "oncology", "drug", "pharmaceutical", "pharmacology",
    "therapy", "therapeutic", "EHR", "electronic health record",
    "medical record", "mental health", "public health", "psychiatry",
    "nursing", "surgery", "surgical", "medical imaging",
    "CT scan", "MRI", "X-ray", "ultrasound", "histopathology",
    "genomics", "triage", "symptom", "physician", "MedQA", "PubMed",
]

# 医疗弱关键词 (在 CS 论文中常有歧义, 如 "diagnostic benchmark" / "health management";
# 不能单独判定, 仅用于补充相关性评分)
MEDICAL_TERMS_WEAK = ["health", "diagnosis", "diagnostic", "treatment"]

_MED_STRONG_RE = re.compile(
    "|".join(r"\b" + re.escape(t) + r"\b" for t in MEDICAL_TERMS_STRONG), re.IGNORECASE)
_MED_WEAK_RE = re.compile(
    "|".join(r"\b" + re.escape(t) + r"\b" for t in MEDICAL_TERMS_WEAK), re.IGNORECASE)


def is_medical(text: str) -> bool:
    """强关键词命中才算医疗论文; 弱关键词不能单独判定。"""
    return bool(_MED_STRONG_RE.search(text))


def medical_score(text: str) -> int:
    """相关性评分: 强词命中数 + 弱词命中数 (仅对已判定的论文排序用)。"""
    return len(_MED_STRONG_RE.findall(text)) + len(_MED_WEAK_RE.findall(text))

# 大模型主题关键词 (与医疗关键词取交集)
TOPIC_TERMS = [
    "large language model", "LLM", "MLLM", "VLM", "LVLM",
    "vision-language model", "GPT", "language model",
    "retrieval-augmented", "retrieval augmented", "RAG",
    "agent", "agentic", "multi-agent", "tool use", "tool learning",
    "benchmark", "evaluation", "leaderboard", "survey",
    "foundation model", "multimodal", "fine-tuning", "alignment",
    "reasoning", "chain-of-thought",
]

_TOPIC_RE = re.compile(
    "|".join(r"\b" + re.escape(t) + r"\b" for t in TOPIC_TERMS), re.IGNORECASE)


def has_topic(text: str) -> bool:
    """必须命中大模型主题词 (arXiv API 的 all: 匹配是词干化的, 会漏进无关论文)。"""
    return bool(_TOPIC_RE.search(text))

# 分类标签: (标签名, 正则列表) —— 按优先级排列, 第一个命中的作为主分类
TAG_RULES = [
    ("📊 评测 / Benchmark", [
        r"benchmark", r"leaderboard", r"\bexam\b",
        r"\bMedQA\b", r"\bMedMCQA\b", r"\bPubMedQA\b", r"\bUSMLE\b",
        r"\bMedBench\b", r"\bMedXpertQA\b", r"\bHealthBench\b",
    ]),
    ("🤖 Agent", [
        r"agentic", r"multi-?agent", r"\bagent(s)?\b", r"tool use", r"tool learning",
    ]),
    ("🔍 RAG", [
        r"retrieval[- ]augmented", r"\bRAG\b", r"knowledge[- ]grounded",
    ]),
    ("🖼️ 多模态医疗", [
        r"multimodal", r"multi-modal", r"vision-language", r"medical imaging",
        r"radiology report", r"pathology image",
    ]),
    ("📚 综述 / Survey", [
        r"\bsurvey\b", r"\breview\b", r"\btutorial\b",
    ]),
]

DEFAULT_TAG = "🩺 医疗 LLM"

# 抓取最近几天的论文 (GitHub Actions 每天跑, 2 天窗口防漏; 周一覆盖周末)
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "2"))
if datetime.now(timezone.utc).weekday() == 0:  # Monday
    LOOKBACK_DAYS = max(LOOKBACK_DAYS, 3)

ARXIV_MAX_RESULTS = 150
HTTP_TIMEOUT = 30
UA = {"User-Agent": "med-llm-digest/1.0 (github actions; contact: repo owner)"}

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

OUT_DIR = "digests"

# ---------------------------------------------------------------- HTTP

def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()

# ---------------------------------------------------------------- arXiv

def build_arxiv_query() -> str:
    med = " OR ".join(f'all:"{t}"' for t in MEDICAL_TERMS_STRONG[:20])  # arXiv 查询长度有限, 取前 20 个
    topic = " OR ".join(f'all:"{t}"' for t in [
        "large language model", "LLM", "retrieval-augmented", "RAG",
        "agent", "agentic", "benchmark", "evaluation", "foundation model",
    ])
    return f"({med}) AND ({topic})"


def fetch_arxiv(since: datetime) -> list[dict]:
    query = build_arxiv_query()
    params = urllib.parse.urlencode({
        "search_query": query,
        "start": 0,
        "max_results": ARXIV_MAX_RESULTS,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    url = f"https://export.arxiv.org/api/query?{params}"
    print(f"[arXiv] GET {url[:120]}...")
    try:
        data = http_get(url)
    except Exception as e:
        print(f"[arXiv] 请求失败: {e}", file=sys.stderr)
        return []

    root = ET.fromstring(data)
    papers = []
    for entry in root.findall(f"{ATOM}entry"):
        arxiv_url = entry.findtext(f"{ATOM}id", "")
        m = re.search(r"arxiv\.org/abs/([^v]+)(v\d+)?", arxiv_url)
        if not m:
            continue
        arxiv_id = m.group(1)
        published_raw = entry.findtext(f"{ATOM}published", "")
        try:
            published = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if published < since:
            continue  # 按提交时间倒序, 后面的更旧, 但仍遍历完 (arXiv 排序不严格)

        title = " ".join((entry.findtext(f"{ATOM}title") or "").split())
        abstract = " ".join((entry.findtext(f"{ATOM}summary") or "").split())
        authors = [a.findtext(f"{ATOM}name", "") for a in entry.findall(f"{ATOM}author")]
        cats = [c.get("term", "") for c in entry.findall(f"{ATOM}category")]

        text = f"{title} {abstract}"
        if not (is_medical(text) and has_topic(text)):
            continue  # arXiv 查询召回偏宽, 本地再过滤一次

        papers.append({
            "id": arxiv_id,
            "title": title,
            "abstract": abstract,
            "authors": authors[:6],
            "published": published.strftime("%Y-%m-%d"),
            "categories": cats,
            "source": "arXiv",
            "upvotes": 0,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
        })
    print(f"[arXiv] 命中 {len(papers)} 篇")
    return papers

# ------------------------------------------------- Hugging Face Daily

def fetch_hf_daily(since: datetime) -> list[dict]:
    papers = []
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(LOOKBACK_DAYS)]
    for day in days:
        url = f"https://huggingface.co/api/daily_papers?date={day}"
        print(f"[HF] GET {url}")
        try:
            data = json.loads(http_get(url))
        except Exception as e:
            print(f"[HF] {day} 请求失败: {e}", file=sys.stderr)
            continue
        for item in data:
            p = item.get("paper") or {}
            title = p.get("title", "")
            abstract = p.get("summary", "") or ""
            text = f"{title} {abstract}"
            if not (is_medical(text) and has_topic(text)):
                continue
            arxiv_id = p.get("id") or ""
            papers.append({
                "id": arxiv_id,
                "title": title,
                "abstract": " ".join(abstract.split()),
                "authors": [a.get("name", "") for a in (p.get("authors") or [])[:6]],
                "published": (p.get("publishedAt") or day)[:10],
                "categories": [],
                "source": "HuggingFace",
                "upvotes": item.get("numUpvotes") or p.get("upvotes") or 0,
                "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else f"https://huggingface.co/papers/{p.get('id','')}",
            })
        time.sleep(1)  # 礼貌限速
    print(f"[HF] 命中 {len(papers)} 篇 (含与 arXiv 重复)")
    return papers

# ------------------------------------------------- 去重 / 打标签 / 排序

def norm_id(pid: str) -> str:
    return re.sub(r"v\d+$", "", pid.strip().lower())


def tag_paper(p: dict) -> list[str]:
    text = f"{p['title']} {p['abstract']}"
    tags = []
    for tag, patterns in TAG_RULES:
        if any(re.search(pt, text, re.IGNORECASE) for pt in patterns):
            tags.append(tag)
    if not tags:
        tags = [DEFAULT_TAG]
    return tags


def merge(arXiv_papers: list[dict], hf_papers: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    for p in arXiv_papers:
        by_id[norm_id(p["id"])] = p
    for p in hf_papers:
        key = norm_id(p["id"])
        if key in by_id:
            old = by_id[key]
            old["upvotes"] = max(old.get("upvotes", 0), p.get("upvotes", 0))
            old["source"] = "arXiv + HF 🔥"
        else:
            by_id[key] = p
    merged = list(by_id.values())
    for p in merged:
        p["tags"] = tag_paper(p)
        p["primary_tag"] = p["tags"][0]
        p["medical_score"] = medical_score(f"{p['title']} {p['abstract']}")
    merged.sort(key=lambda p: (p.get("upvotes", 0) * 3 + p["medical_score"] + len(p["tags"]),
                               p["published"]), reverse=True)
    return merged

# ------------------------------------------------- LLM 中文摘要 (可选)
#
# 设置以下环境变量即启用 (未设置则跳过, 不影响其他功能):
#   LLM_API_KEY   必填, OpenAI 兼容接口的 key
#   LLM_BASE_URL  可选, 默认 https://api.openai.com/v1 (DeepSeek/通义/Kimi 等兼容接口均可)
#   LLM_MODEL     可选, 默认 gpt-4o-mini

LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
SUMMARIZE_MAX = int(os.environ.get("SUMMARIZE_MAX", "40"))  # 每天最多摘要多少篇


def summarize_one(p: dict) -> str:
    """调用 OpenAI 兼容接口生成一句话中文 TL;DR, 失败返回空串。"""
    prompt = (
        "你是医疗 AI 方向的论文编辑。请用一句中文(≤60字)总结下面这篇论文,"
        "格式为: 针对<问题>, 提出<方法>, 取得<结果/意义>。不要夸大, 不要翻译标题。\n\n"
        f"标题: {p['title']}\n摘要: {p['abstract'][:2000]}"
    )
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 120,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        f"{LLM_BASE_URL}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {LLM_API_KEY}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip().strip('"')
    except Exception as e:
        print(f"[LLM] 摘要失败 ({p['id']}): {e}", file=sys.stderr)
        return ""


def summarize_papers(papers: list[dict]) -> None:
    if not LLM_API_KEY:
        print("[LLM] 未设置 LLM_API_KEY, 跳过中文摘要")
        return
    print(f"[LLM] 使用 {LLM_MODEL} @ {LLM_BASE_URL} 生成中文摘要...")
    for i, p in enumerate(papers[:SUMMARIZE_MAX]):
        p["tldr_zh"] = summarize_one(p)
        if i % 5 == 4:
            print(f"[LLM] {i + 1} 篇已摘要")
            time.sleep(1)
    print(f"[LLM] 完成 {sum(1 for p in papers if p.get('tldr_zh'))} 篇摘要")


# ------------------------------------------------- 输出

def render_markdown(papers: list[dict], today: str) -> str:
    lines = [
        f"# 🏥 医疗大模型日报 · {today}",
        "",
        f"> 数据源: arXiv + Hugging Face Daily Papers · 共 **{len(papers)}** 篇",
        f"> 关注方向: 医疗 LLM / RAG / Agent / 评测·Benchmark / 多模态医疗 / 综述",
        "",
    ]
    tag_order = [t for t, _ in TAG_RULES] + [DEFAULT_TAG]
    for tag in tag_order:
        group = [p for p in papers if p["primary_tag"] == tag]
        if not group:
            continue
        lines += [f"## {tag} ({len(group)})", ""]
        for p in group:
            votes = f" · 👍 {p['upvotes']}" if p.get("upvotes") else ""
            lines.append(f"### [{p['title']}]({p['url']})")
            authors = ", ".join(p["authors"][:3]) + (" et al." if len(p["authors"]) > 3 else "")
            lines.append(f"- {p['published']} · {p['source']}{votes} · {authors}")
            if len(p["tags"]) > 1:
                lines.append(f"- 标签: {' / '.join(p['tags'])}")
            abstract = p["abstract"]
            if p.get("tldr_zh"):
                lines.append(f"- 💡 **{p['tldr_zh']}**")
            if len(abstract) > 600:
                abstract = abstract[:600].rsplit(" ", 1)[0] + " …"
            lines.append(f"- {abstract}")
            lines.append(f"- [alphaXiv 解读](https://www.alphaxiv.org/abs/{norm_id(p['id'])})" if p["id"] else "")
            lines.append("")
    if len(lines) < 6:
        lines.append("_今日无新增相关论文。_")
    return "\n".join(lines)


def main() -> None:
    since = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"抓取窗口: 最近 {LOOKBACK_DAYS} 天 (since {since.date()})")

    arxiv_papers = fetch_arxiv(since)
    time.sleep(3)  # arXiv 官方建议请求间隔
    hf_papers = fetch_hf_daily(since)
    papers = merge(arxiv_papers, hf_papers)
    print(f"去重后共 {len(papers)} 篇")
    summarize_papers(papers)

    os.makedirs(OUT_DIR, exist_ok=True)
    md_path = os.path.join(OUT_DIR, f"{today}.md")
    json_path = os.path.join(OUT_DIR, f"{today}.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(papers, today))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)
    print(f"已写入 {md_path} / {json_path}")


if __name__ == "__main__":
    main()
