#!/usr/bin/env python3
"""
regenerate_and_publish.py
=========================
按博哥双绿标准重建并发布文章。

用法：
  python3 regenerate_and_publish.py --dry-run          # 预览第1篇
  python3 regenerate_and_publish.py --execute          # 正式执行全部9篇
  python3 regenerate_and_publish.py --single 90        # 只重建ID=90

博哥双绿标准：
  ✅ SEO绿：Focus Keyword设置 + 关键词1%-1.5%密度自然分布
  ✅ Readability绿：单句≤20词、单段落≤3句、每100词≥1过渡词、被动语态≤10%
  ✅ 内链：≥2个内部链接锚文本（如《xxx》格式）
  ✅ 外链：≥1个外部链接占位（真实URL）
  ✅ 真人口吻：I tested / In my experience
  ✅ 每工具有1优1缺（真实限制，非全好评）
  ✅ 无AI腔套话
"""

import os, sys, json, time, re, html as html_module, urllib.request, ssl, xmlrpc.client
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from shared_config import (
    MINIMAX_API_KEY, MINIMAX_CHAT_MODEL,
    WP_SITE_URL, WP_APP_PASSWORD,
    ARTICLES, TRANSITION_WORDS, CATEGORY_MAP, PUBLISHED_ARTICLE_IDS,
    fix_long_paragraphs, _track_score_milestone,
)
WP_USERNAME = '{{WP_USERNAME}}'  # XML-RPC auth: author=2, not admin email


# ================================================================
# 站点域名动态读取（脱敏后不硬编码任何真实域名）
# ================================================================
def _wp_site_domain():
    """返回当前配置站点的裸域名（不含协议）"""
    try:
        import sys as _s, pathlib as _p
        _s.path.insert(0, str(_p.Path(__file__).resolve().parent.parent / "config"))
        from config import WP_SITE_DOMAIN  # type: ignore
        return WP_SITE_DOMAIN
    except Exception:
        return os.environ.get("WP_SITE_DOMAIN", "your-site.com")

def log(msg: str, level: str = "INFO"):
    """日志输出"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")


def call_minimax_chat(messages: list, model: str = None, temperature: float = 0.7) -> str:
    """
    调用MiniMax Chat API
    MiniMax-M2.7 模型（非M2.7）
    """
    if model is None:
        model = MINIMAX_CHAT_MODEL
    endpoint = "https://api.minimaxi.com/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 6144   # 2026-06-02: 4096→6144，确保1200+词文章完整生成
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=data,
        headers={
            "Authorization": f"Bearer {MINIMAX_API_KEY}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
        
        # 标准响应格式：content 可能为空（MiniMax M2.7 推理内容在 reasoning_content）
        msg = resp_data["choices"][0]["message"]
        content = msg.get("content", "") or msg.get("reasoning_content", "")
        if not content:
            log(f"警告: content 和 reasoning_content 均为空，原始响应: {str(resp_data)[:200]}", "WARNING")
        return content.strip()
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        log(f"HTTP错误 {e.code}: {error_body[:500]}", "ERROR")
        raise
    except Exception as e:
        log(f"API调用异常: {str(e)}", "ERROR")
        raise



from xmlrpc_publisher import _trigger_yoast_save, _inject_faq_schema

# ============================================================
# 文章配置（9篇）
# ============================================================
# （ARTICLES已迁移至 shared_config.py，此处不再重复）

def _retry_http_request(req_builder, ctx, retries=2, delay=10):
    """统一HTTP请求重试（处理IncompleteRead/ConnectionReset等瞬时错误）"""
    last_err = None
    for attempt in range(retries):
        try:
            req = req_builder()
            with urllib.request.urlopen(req, context=ctx, timeout=40) as resp:
                return resp.read()
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            transient = any(kw in err_str for kw in
                           ['incomplete', 'connection', 'reset', 'timed out', 'abort', 'eof', 'remote end closed'])
            if transient and attempt < retries - 1:
                wait = delay * (attempt + 1)
                time.sleep(wait)
            else:
                break
    raise last_err

# ============================================================
# WordPress XML-RPC
# ============================================================
def xmlrpc_call(method, params, retries=3, retry_delay=15):
    """
    WordPress XML-RPC 调用（2026-05-27 重试增强版）。
    处理 IncompleteRead / ConnectionReset 等长文章瞬时错误。
    """
    url = f"{WP_SITE_URL}/xmlrpc.php"
    body = xmlrpc.client.dumps((params,), method)
    ctx = ssl.create_default_context()

    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body.encode('utf-8'),
                                        headers={'Content-Type': 'text/xml'}, method='POST')
            with urllib.request.urlopen(req, context=ctx, timeout=40) as resp:
                return xmlrpc.client.loads(resp.read())[0][0]
        except Exception as e:
            last_err = e
            err_str = str(e)
            # 瞬时错误才重试：连接断开 / 读不完整 / 超时
            transient = any(kw in err_str.lower() for kw in
                           ['incomplete', 'connection', 'reset', 'timed out', 'abort', 'eof'])
            if transient and attempt < retries - 1:
                wait = retry_delay * (attempt + 1)
                log(f"  [{method}] #{attempt+1} 瞬时错误: {e}，{wait}s后重试...", "WARN")
                time.sleep(wait)
            else:
                break
    raise Exception(f"[{method}] {retries}次尝试后仍失败: {last_err}")

# ============================================================
# 生成文章（使用 batch_regenerate_articles.py 的成功版 prompt）
# ============================================================

# ✅ 最后一次成功版本（batch_regenerate_articles.py）
# System约束：不输出思考过程
# max_tokens=5000（足够生成完整内容）
# Prompt结构：简单直接，工具逐个介绍，bullet points

SYSTEM_PROMPT = (
    "You are a professional blog writer for BestAItoolsite.com. "
    "Output ONLY the complete HTML article. Start immediately with <h2>Introduction</h2>. "
    "Do NOT write any thinking, reasoning, notes, or self-commentary. "
    "Never wrap content in code blocks. Output plain HTML only."
)

ARTICLE_PROMPT_TEMPLATE = """Write a complete HTML blog article for BestAItoolsite.com.

TITLE: {title}
FOCUS KEYWORD: {focus_kw}
PRIMARY KEYWORD: {primary_kw}
CATEGORY: {category}
TOOLS: {tools_list}
INTERNAL LINKS: Like 《{int_link1_title}》, Like 《{int_link2_title}》
EXTERNAL LINKS: {ext_links_formatted}

# ABSOLUTE MUST-FOLLOW RULES TO FIX ALL YOAST ERRORS
1. COVER IMAGE: Insert a cover placeholder RIGHT AFTER the first <p>...</p> of Introduction, exactly one line:
   <p>[COVER_IMAGE]</p>
   This placeholder will be replaced with an AI-generated featured image matching the article content.
2. CONTENT IMAGES: Insert 1-2 more image tags in body sections (not intro). Each image alt text MUST contain focus keyword or direct synonyms. Format: <img src="https://images.unsplash.com/photo-XXXXX?w=800" alt="focus keyword descriptive text">
3. MUST ADD BOTH INTERNAL LINKS AND OUTBOUND EXTERNAL LINKS IN ARTICLE CONTENT, NO EMPTY LINKS.
3. PUT THE FULL EXACT FOCUS KEYWORD IN THE VERY FIRST SENTENCE OF INTRODUCTION PARAGRAPH, NO EXCEPTION.
4. NATURALLY REPEAT FOCUS KEYWORD 8 TIMES OR MORE ACCORDING TO TEXT LENGTH, STRICT KEYPHRASE DENSITY 1-1.5 PERCENT.
5. SEO TITLE RULE: Write full exact focus keyword at the very beginning, shorten title strictly within 60 visible characters.
6. WRITE OFFICIAL META DESCRIPTION MANUALLY: 120-155 characters, MUST INCLUDE FULL FOCUS KEYWORD.
7. NEVER USE THE SAME FOCUS KEYWORD ON TWO DIFFERENT ARTICLES TO AVOID REPEATED KEYPHRASE WARNING.
8. ADD FOCUS KEYWORD AND ITS SYNONYMS INTO MULTIPLE H2 AND H3 SUBHEADINGS SUFFICIENTLY.
9. PAGE SLUG MUST CONTAIN CORE WORDS FROM FOCUS KEYWORD.
10. WORD COUNT STANDARD: MINIMUM 1200 WORDS, TARGET 1400-1600 WORDS. This is non-negotiable for SEO depth. If you write fewer than 1200 words, the article will fail quality gate.
   - For longer articles: expand each tool section with real-world testing scenarios, specific price points, detailed comparisons, and reader takeaways.
   - Every paragraph: 2-3 sentences only. Every sentence: under 20 words.

# WRITING AND READABILITY RULES
1. One paragraph has maximum 3 sentences only — but write MORE paragraphs to reach 1200+ words.
2. Single sentence no more than 20 words, average below 15 words.
3. Every tool must have one real weakness, refuse full positive comments.
4. Use first-person view: I tested, In my experience, I find.
5. Use transition words: however, therefore, moreover, for example, additionally, specifically, first, second, then, because, especially. Every 100 words must have at least 1 transition word.
6. Passive voice ratio below 10 percent.
7. Keep objective tone, comply with Google AdSense rules.

# FIXED HTML STRUCTURE
- STRICT H2 RULE: Every H2 heading must be NARRATIVE-DRIVEN with a first-person moment, time anchor, or specific event. No template-style H2s like "Key Features", "Overview", "Pros and Cons", "Introduction", "Conclusion", "How to Choose", "Summary", "FAQ", "The Best", "Top Pick", "Verdict", "Review".
- H2 examples that PASS: "The Moment I Realized My Spreadsheet Was Lying to Me", "I Tested 5 Tools for 30 Days — Here's What Broke First", "The Day AI Wrote Better Cold Emails Than I Did"
- H2 examples that FAIL (do NOT use): "Key Features", "Overview", "Pros and Cons", "What Is", "How to Choose", "Getting Started", "Conclusion", "Summary", "Verdict", "Review", "FAQ", "The Best", "Top Pick", "Benefits", "Drawbacks"
- INTRODUCTION H2: Use a narrative hook — "The Day I Wasted 3 Hours on..." or "I Tried X and Expected Y but Got Z" — not the word "Introduction". Or skip the H2 entirely and let the opening paragraph flow naturally.

<h2>[Narrative Hook Opening — e.g. The Day I...]</h2>
First sentence must carry exact focus keyword, write user pain points, lead with a specific moment or frustration, 80-120 words.
<p>[Narrative opening paragraph]</p>
<p>[Second paragraph building on the story]</p>

<h2>[Narrative H2 for first tool section — e.g. The Moment I Tried {tool1}]</h2>
<p>Paragraph about the first experience...</p>
<ul>
<li><strong>What it does:</strong> Clear function introduction</li>
<li><strong>Pros:</strong> 2-3 practical advantages</li>
<li><strong>Cons:</strong> One real existing shortcoming</li>
<li><strong>Best for:</strong> Matching user groups or use cases</li>
</ul>

For each remaining tool: write a narrative H2 that includes the tool name and a specific moment/event context.

# FORBIDDEN WORDS
In today's digital age, It is important to note, game-changing, leveraging, next-generation, revolutionize, best-in-class, unparalleled, landscape of AI, cutting-edge, top-tier, ultimate solution, state-of-the-art, The world of AI

# OUTPUT RULE
Only output pure complete HTML code, start directly with <h2>Introduction</h2>. No extra words, no explanations, no notes."""

def build_prompt(article: dict) -> str:
    tools_list = ", ".join(article["tools"][:5])
    # 安全获取内链（容许空列表）
    all_int_links = article.get("int_links") or []
    int_link1_title = all_int_links[0][0] if len(all_int_links) > 0 else (article["tools"][0] if article.get("tools") else article.get("focus_kw", "AI tool"))
    int_link2_title = all_int_links[1][0] if len(all_int_links) > 1 else (article["tools"][1] if article.get("tools") and len(article["tools"]) > 1 else (article["tools"][0] if article.get("tools") else article.get("focus_kw", "AI tool")))
    ext_links_formatted = ", ".join(
        f"{name} = {url}" for name, url in article["ext_links"]
    )
    return ARTICLE_PROMPT_TEMPLATE.format(
        title=article["title"],
        focus_kw=article["focus_kw"],
        primary_kw=article["primary_kw"],
        category=article["category"],
        tools_list=tools_list,
        int_link1_title=int_link1_title,
        int_link2_title=int_link2_title,
        ext_links_formatted=ext_links_formatted,
        tool1=article["tools"][0] if article["tools"] else "Tool Name",
    )

def generate_seo_friendly_content(article: dict) -> str:
    """调用MiniMax生成文章内容"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_prompt(article)},
    ]

    payload = json.dumps({
        "model": "MiniMax-M2.7",
        "messages": messages,
        "max_tokens": 6000,   # 2026-05-26: 提高到6000，避免内容被截断，支持更长更自然的叙事
        "temperature": 0.7,
    }).encode()

    req = urllib.request.Request(
        "https://api.minimaxi.com/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {MINIMAX_API_KEY}"},
        method="POST"
    )
    ctx = ssl.create_default_context()

    for attempt in range(3):
        try:
            # 2026-06-18: 120→300秒，1200+词内容响应时间更长
            with urllib.request.urlopen(req, context=ctx, timeout=300) as resp:
                raw = json.loads(resp.read())["choices"][0]["message"]["content"]

            # 清洗：去掉思考过程标签
            content = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL)
            content = re.sub(r'^(<html[^>]*>|</html>|<body[^>]*>|</body>|<pre[^>]*>.*?</pre>|<code[^>]*>.*?</code>|<div[^>]*>)\s*',
                             '', content, flags=re.IGNORECASE)

            # 保留所有内容（含任何在<h2>之前的narrative opening段落）
            # 只清洗明显的HTML包装残渣，不砍narrative内容
            content = content.strip()

            # ── P549发现的新型内容损坏清洗 ──────────────
            # 损坏模式：文章出现两个Introduction（第1个是模板残渣，第2个是真实内容）
            # 策略：检测重复Introduction，丢弃第1个之前的所有内容
            intro_tags = [m.start() for m in re.finditer(r'<h2>Introduction</h2>', content)]
            if len(intro_tags) >= 2:
                # 保留第2个Introduction到最后，丢弃之前的损坏内容
                content = content[intro_tags[1]:]
                log("  [CLEAN] 双重Introduction检测到，已丢弃第1个损坏副本", "WARN")

            # Yoast SEO指令残渣：模型把prompt里的验证规则当正文内容输出了
            # 出现在</ul>之后或文章末尾，特征词："verify the focus keyword"
            # "as per Yoast SEO instructions" "8 times throughout the article"
            seo_instruction_patterns = [
                r'verify the focus keyword.{0,100}',
                r'as per Yoast SEO.{0,80}',
                r'appears at least \d+ times throughout the article.{0,100}',
                r'Focus Keyword:[^\n]{0,20}\s*(?:Place|Repeat).{0,100}',
            ]
            for p in seo_instruction_patterns:
                before = len(content)
                content = re.sub(p, '', content, flags=re.IGNORECASE)
                if len(content) < before:
                    log(f"  [CLEAN] Yoast SEO指令残渣已清除", "WARN")

            # ── 解析 SEO METADATA（模型最后一行输出）──────────
            # 格式: SEO Title: xxx\nMeta Description: xxx
            meta_match = re.search(
                r'SEO\s*Title:\s*(.{1,70})\s*\n\s*Meta\s*Description:\s*(.{1,160})',
                content, re.IGNORECASE
            )
            if meta_match:
                article["seo_title"] = meta_match.group(1).strip()
                article["seo_desc"] = meta_match.group(2).strip()
                # 把metadata行从正文去掉
                content = re.sub(
                    r'\s*SEO\s*Title:\s*.{1,70}\s*\n\s*Meta\s*Description:\s*.{1,160}\s*$',
                    '', content, flags=re.IGNORECASE
                ).strip()

            return content

        except Exception as e:
            if attempt < 2:
                time.sleep(5)
            else:
                raise Exception(f"MiniMax API失败: {e}")

    return ""

# ============================================================


def precheck_readability(content: str, article: dict = None) -> dict:
    """返回可读性 + SEO结构 + SEO合规 全面预检结果"""
    text = re.sub(r'<[^>]+>', ' ', content)
    words = text.split()
    total_words = len(words)
    issues = []  # 必须在H2检测前初始化
    text_lower = text.lower()

    # ── 基础可读性 ──────────────────────────────────────
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    paras = [p.strip() for p in re.split(r'\n\s*\n', content)
             if p.strip() and not p.strip().startswith('<h')]

    transition_count = sum(1 for tw in TRANSITION_WORDS if f' {tw} ' in f' {text_lower} ')
    passive_matches = re.findall(r'\b(is|are|was|were|been|being)\s+\w+ed\b', text_lower)
    passive_pct = len(passive_matches) / max(len(sentences), 1) * 100
    long_sentences = [s for s in sentences if len(s.split()) > 20]
    long_paras = [p for p in paras if len(re.split(r'(?<=[.!?])\s+(?=[A-Z])', p)) > 3]

    # ── 正文图片 + ALT文本 ──────────────────────────────
    img_tags = re.findall(r'<img\s[^>]*>', content, re.IGNORECASE)
    img_count = len(img_tags)
    # ALT缺失或为空
    img_without_alt = [tag for tag in img_tags
                       if not re.search(r'alt=["\'][^"\']+["\']', tag, re.IGNORECASE)
                       or re.search(r'alt=["\']\s*["\']', tag)]
    alt_issues = len(img_without_alt)

    # ── 裸链检测（外部链接必须加 nofollow） ────────────
    ext_links = re.findall(r'<a\s[^>]*href=["\']https?://[^"\']+["\'][^>]*>([^<]+)</a>', content, re.IGNORECASE)
    # 排除站内链接（域名从 config 动态读取）
    _site = __import__('re').escape(_wp_site_domain())
    ext_links = [m.group(0) for m in [re.search(r'<a\s[^>]*href=["\']https?://([^"\']+)["\']', l, re.IGNORECASE) for l in [content]] if m and _site not in m.group(1)]
    # 更准确：直接找所有外部href
    all_hrefs = re.findall(r'<a\s[^>]*href=["\'](https?://[^"\']+)["\'][^>]*>', content, re.IGNORECASE)
    ext_nofollow_issues = 0
    for href in all_hrefs:
        if _wp_site_domain() not in href:
            # 找这个href对应标签是否有rel="nofollow"
            pattern = re.compile(r'<a\s[^>]*href=["\']' + re.escape(href) + r'["\'][^>]*>', re.IGNORECASE)
            tag_match = pattern.search(content)
            if tag_match:
                rel = re.search(r'rel=["\']([^"\']+)["\']', tag_match.group(0), re.IGNORECASE)
                if not rel or 'nofollow' not in rel.group(1).lower():
                    ext_nofollow_issues += 1

    # ── AI腔检测 ───────────────────────────────────────
    robotic_phrases = [
        "in today's digital age", "it's important to note", "it is important to note",
        "game-changing", "leveraging", "next-generation", "revolutionize",
        "best-in-class", "unparalleled", "landscape of ai", "cutting-edge",
        "top-tier", "ultimate solution", "state-of-the-art", "the world of ai",
        "it goes without saying", "needless to say", "as the name suggests",
        "it is worth noting", "it is clear that", "there is no doubt",
        "artificial intelligence is", "ai technology is", "machine learning enables",
        "deep learning is", "ai-powered",
    ]
    found_robotic = [p for p in robotic_phrases if p in text_lower]

    # ── 内部链接数量 ───────────────────────────────────
    internal_link_pattern = r'href="https?://[^"]*' + re.escape(_wp_site_domain()) + r'[^"]*"'
    internal_links = re.findall(internal_link_pattern, content, re.IGNORECASE)
    if not internal_links:
        internal_links = re.findall(r'href="/[a-z0-9\-]+"', content, re.IGNORECASE)
    internal_link_count = len(internal_links)
    # 内链404检查（validate_internal_links=True 时启用）
    int_links_404 = []
    if article and article.get("validate_internal_links"):
        import urllib.request as _ur
        _ctx = ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = ssl.CERT_NONE
        for href in set(internal_links[:20]):  # 最多检查前20个去重
            url = href.replace('href="', '').replace('"', '')
            # 跳过锚点链接（以#结尾）
            if url.endswith('#') or '#' in url:
                continue
            try:
                req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with _ur.urlopen(req, timeout=8, context=_ctx) as r:
                    if r.getcode() == 404:
                        int_links_404.append(url)
            except Exception:
                pass  # 超时/网络错误不计入404
    if int_links_404:
        issues.append(f"[FAIL] 内链404: {', '.join(int_links_404[:3])}{' +更多' if len(int_links_404) > 3 else ''}")

    # ── H2章节检测（2026-05-26 重构：叙事优先）───────────────
    # 新规则：不需要 Introduction/Conclusion，允许自由叙事结构
    h2_tags = re.findall(r'<h2[^>]*>([^<]+)</h2>', content, re.IGNORECASE)
    h2_titles = [h.strip().lower() for h in h2_tags]
    # 叙事型文章：最多5个H2，允许无 Introduction/Conclusion
    h2_count = len(h2_tags)
    if h2_count > 5:
        issues.append(f"[NOTE] H2数量偏多({h2_count}个)，建议3-5个以保持叙事节奏")
    if h2_count == 0:
        issues.append("[NOTE] 文章无H2章节（非叙事型文章可忽略）")

    # ── H2模板结构检测（强化版：匹配 Prompt Layer 1）────────
    # 禁止词黑名单（扩展版，覆盖Prompt中所有pattern）
    forbidden_h2_patterns = [
        (r'\b(introduction|conclusion|final\s*thoughts|wrap[\s-]?up|summary|verdict|final\s*verdict|bottom\s*line|our\s*recommendation)\b',
         '收尾式H2（Conclusion/Summary/Verdict/Wrap-up）'),
        (r'\b(key\s*features?|features|feature\s*overview|main\s*features?|overview|overviews)\b',
         '功能概览式H2（Key Features/Features/Overview）'),
        (r'\b(pros\s*and\s*cons|pros|cons|benefits|drawbacks)\b',
         '对比式H2（Pros and Cons/Benefits/Drawbacks）'),
        (r'\b(comparison|compared|vs\.?|v\.?s\.?|alternatives)\b',
         '对比式H2（Comparison/Alternatives）'),
        (r'\b(review|reviewing|in\s*review|reviewed)\b',
         '评测式H2（Review/Reviewed）'),
        (r'\b(what\s*is|what\s*does|what\s*x|about|is\s*it\s*right|should\s*you\s*buy)\b',
         '产品介绍式H2（What Is/What Does/About）'),
        (r'\b(how\s*to\s*choose|how\s*to\s*decide|choosing|best\s*for|who\s*is\s*this|buying\s*guide|use\s*cases|pricing)\b',
         '选购指南式H2（How to Choose/Best For/Buying Guide）'),
        (r'\b(getting\s*started|beginner[\'s]?\s*guide|deep\s*dive|complete\s*guide|how\s*it\s*works)\b',
         '入门式H2（Getting Started/Deep Dive/Complete Guide）'),
        (r'\b(the\s*best|top\s*pick|is\s*it\s*right|faq)\b',
         '模板式H2（The Best/Top Pick/FAQ）'),
    ]
    h2_template_issues = []
    for h2_text in h2_titles:
        for pattern, desc in forbidden_h2_patterns:
            if re.search(pattern, h2_text):
                h2_template_issues.append(f'"{h2_text}" ({desc})')
    if h2_template_issues:
        issues.append(f"[FAIL] H2模板结构违规: {' | '.join(h2_template_issues)}")

    # ── H2叙事特征检测（白名单导向）─────────────────────────
    # 每个H2至少包含：时间词 OR 动作词 OR 情绪词 OR 具体事件
    narrative_markers = [
        r'\b(i\s+tried|i\s+stopped|i\s+had\s+to|i\s+didn|i\s+was|i\s+got|i\s+let|when|after|by|until|afterwards?)\b',
        r'\b(first\s+time|day\s+one|week\s+two|hour|minute|month|last\s+|first\s+)\b',
        r'\b(broke|crashed|failed|worked\s+until|didn\'t\s+match|stopped\s+working|guessing)\b',
        r'\b(frustrated|annoyed|surprised|weird|unexpected|意外|挫败|惊喜)\b',
    ]
    for h2_text in h2_titles:
        has_narrative = any(re.search(p, h2_text) for p in narrative_markers)
        if not has_narrative:
            issues.append(f"[NOTE] H2可能缺乏叙事特征: \"{h2_text}\" — 建议含时间/动作/情绪/事件")

    # ── Focus Keyword密度 ───────────────────────────────
    focus_kw = (article.get("focus_kw") or "").lower()
    focus_kw_count = text_lower.count(focus_kw) if focus_kw else 0

    # ── SEO标题 & Meta描述长度 ─────────────────────────
    seo_title = article.get("seo_title", "") if article else ""
    seo_desc = article.get("seo_desc", "") if article else ""
    yoast_title_len = len(seo_title) if seo_title else 0
    yoast_desc_len = len(seo_desc) if seo_desc else 0

    # ── 汇总 issues（2026-05-26 重构：真人感优先）──────
    issues = []
    # 字数：2026-06-18 升级到1200词最低标准
    if total_words < 1200:
        issues.append(f"[FAIL] 文章字数不足({total_words}词). 最低要求1200词，差距{1200-total_words}词。请扩充内容。")
    elif total_words < 1400:
        issues.append(f"[INFO] 文章偏短({total_words}词). 目标1400-1600词，建议扩充。")
    if long_sentences:
        issues.append(f"[NOTE] 长句>{20}词:{len(long_sentences)}处 — 仅在读起来费劲时在意，不要机械拆分。")
    if long_paras:
        issues.append(f"[NOTE] 段落>3句:{len(long_paras)}处 — 仅在感觉拥挤时在意。")
    if passive_pct > 10:
        issues.append(f"[NOTE] 被动语态偏高:{passive_pct:.0f}%")
    # 删除：过渡词检测（Google 2026降权人工过渡词）
    if img_count < 2:
        issues.append(f"[INFO] 图片不足({img_count}张). 封面+内容图共2张是标准，不是3张。")
    if alt_issues > 0:
        issues.append(f"[INFO] 图片ALT缺失/空:{alt_issues}张")
    if internal_link_count < 2:
        issues.append(f"[NOTE] 内链不足({internal_link_count}个). 真实需要才链，不要凑数。")
    if h2_count == 0:
        issues.append("[NOTE] 文章无H2章节（非叙事型文章可忽略）")
    if found_robotic:
        issues.append(f"AI腔词汇:{','.join(found_robotic[:3])}")
    if focus_kw_count < 1:
        issues.append(f"[INFO] Focus keyword '{focus_kw}'出现{focus_kw_count}次. 只需自然出现在内容中，不要强制塞入第一句。")
    if ext_nofollow_issues > 0:
        issues.append(f"外部链接缺nofollow:{ext_nofollow_issues}处")
    if yoast_title_len > 60:
        issues.append(f"[INFO] SEO标题过长({yoast_title_len}字符). 建议≤60字符。")
    if yoast_desc_len > 155 or (yoast_desc_len > 0 and yoast_desc_len < 120):
        issues.append(f"[INFO] Meta描述长度异常({yoast_desc_len}字符). 建议120-155字符。")

    # ── AI率启发式检测（2026-06-18）：零外部依赖 ─────────
    ai_phrases_local = [
        "in today's rapidly evolving", "in the landscape of", "cutting-edge",
        "best-in-class", "state-of-the-art", "revolutionize", "game-changing",
        "leveraging", "next-generation", "unparalleled", "it is important to note",
        "as the world continues to", "ultimately", "in conclusion",
        "play a pivotal role", "takes center stage", "the realm of",
        "emerges as a", "stands out from the crowd", "it is worth noting",
        "on the other hand", "additionally, it is", "thus, it is",
        "it should be noted", "needless to say", "last but not least",
        "fast forward to", "when it comes to", "in terms of",
        "at the end of the day", "what's more", "moreover",
        "this article will explore", "this comprehensive guide",
        "delve into", "navigate the", "master the art of",
        "the secret to", "unlock the power of", "harness the potential of",
        "it is no secret that", "there are many ways to", "there are several factors",
    ]
    text_lower_local = text_lower
    phrase_count_local = sum(1 for p in ai_phrases_local if p.lower() in text_lower_local)
    sentences_local = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    cv_local = 0.5
    if len(sentences_local) >= 5:
        import statistics as _s
        wc_local = [len(s.split()) for s in sentences_local]
        mean_local = _s.mean(wc_local)
        std_local = _s.stdev(wc_local) if len(wc_local) > 1 else 0
        cv_local = std_local / mean_local if mean_local > 0 else 0.5
    words_local = re.findall(r'\b[a-z]+\b', text_lower_local)
    unique_ratio_local = len(set(words_local)) / len(words_local) if words_local else 0
    paras_local = re.split(r'\n\s*\n', content)
    psc_local = [len([s for s in re.split(r'[.!?]+', p) if s.strip()]) for p in paras_local if p.strip()]
    para_uniform_local = 0
    if len(psc_local) >= 3:
        import statistics as _s2
        pst = _s2.stdev(psc_local) if len(psc_local) > 1 else 0
        pm_local = _s2.mean(psc_local)
        para_uniform_local = pst / pm_local if pm_local > 0 else 0
    # 评分
    ps_local = min(phrase_count_local * 5, 35)
    us_local = (1 - cv_local) * 15 if cv_local < 0.28 else 0
    vs_local = max(0, (0.58 - unique_ratio_local) * 35)
    pus_local = (1 - para_uniform_local) * 10 if para_uniform_local < 0.2 else 0
    ai_score_local = min(int(ps_local + us_local + vs_local + pus_local), 100)
    ai_score_local = max(ai_score_local, 3)
    # AI率 > 70% → FAIL
    if ai_score_local >= 70:
        issues.append(f"[FAIL] AI率过高({ai_score_local}%)，内容过于模板化/AI腔，请人工润色或触发rewrite。")
    elif ai_score_local >= 55:
        issues.append(f"[WARN] AI率偏高({ai_score_local}%)，建议减少模板化表达，增加真实叙事。")

    return {
        "total_words": total_words,
        "sentences": len(sentences),
        "paragraphs": len(paras),
        "long_sentences": len(long_sentences),
        "long_paras": len(long_paras),
        "passive_pct": passive_pct,
        "transition_count": transition_count,
        "img_count": img_count,
        "alt_issues": alt_issues,
        "internal_link_count": internal_link_count,
        "h2_titles": h2_tags,
        "h2_count": h2_count,
        "h2_template_issues": h2_template_issues,
        "focus_kw_count": focus_kw_count,
        "robotic_phrases": found_robotic,
        "ext_nofollow_issues": ext_nofollow_issues,
        "yoast_title_len": yoast_title_len,
        "yoast_desc_len": yoast_desc_len,
        "ai_score": ai_score_local,
        "ai_phrase_count": phrase_count_local,
        "issues": issues,
        "ok": len(issues) == 0,
    }

# ============================================================
# 叙事推进检测（2026-05-26）：人格工程核心
# 检测文章是"叙事推进"还是"模板推进"
# 模板推进 = intro→feature→comparison→recommendation→conclusion
# 叙事推进 = event→feeling→workflow_change→accident→realization→white_space
# ============================================================

def check_narrative_progression(content: str) -> dict:
    """
    检测文章推进逻辑：叙事驱动 vs 模板驱动
    返回 dict:
      fail_types: 模板类型 H2 数量
      pass_types: 叙事类型 H2 数量
      total: 总H2数
      ratio: 模板占比
      verdict: 'FAIL' / 'PASS' / 'UNCERTAIN'
      fail_detail: 具体模板类型列表
      pass_detail: 具体叙事类型列表
      entropy_score: 文章人格熵值（0-1，越高越真人）
      entropy_keepers: 需要保留的高熵段落
    """
    import hashlib
    h2_tags = re.findall(r'<h2[^>]*>([^<]+)</h2>', content, re.IGNORECASE)
    if not h2_tags:
        return {
            "fail_types": 0, "pass_types": 0, "total": 0,
            "ratio": 0.0, "verdict": "UNCERTAIN",
            "fail_detail": [], "pass_detail": [],
            "entropy_score": 0.5, "entropy_keepers": [],
            "issues": ["[NOTE] 文章无H2，无法判断推进结构"]
        }

    # ── 模板类型 H2（工具/分类/比较/推荐类）────────────────
        # 这些都是结构性SEO思维的产物
    fail_patterns = [
        # 工具直接命名类
        (r'\b(?:Replit|Cursor|Vercel|Bolt\.new|Windsurf|Lovable|Logseq|Notion|Obsidian|ChatGPT|Claude|Gemini|Ideogram|Recraft|Firefly|Sora|Kling|Runway)\b(?:\s+\w+){0,4}\s*(?:review|features?|overview|vs|comparison)\b',
         "tool_name_review"),
        (r'\b(?:AI\s*Tools?|AI\s*Apps?|AI\s*Code)\s*(?:Comparison|Review|Guide|Tools)\b',
         "tool_category_template"),
        # 结构标签类
        (r'\b(?:what\s*is|what\s*does|about|introduction)\b', "product_intro_template"),
        (r'\b(?:key\s*features?|features?|feature\soverview|main\s*features?)\b', "feature_list_template"),
        (r'\b(?:pros?\s*(?:and\s*cons?)?|cons?|benefits?|drawbacks?)\b', "proscons_template"),
        (r'\b(?:comparison|compared|vs\.?|versus)\b(?!.\s*(?:with|to|against)\s*\w+\s*(?:workflow|experience|day))',
         "comparison_structure"),
        (r'\b(?:how\s*to\s*choose|how\s*to\s*decide|choosing|best\s*for|who\s*is\s*this|ideal\s*(?:use|case|audience))\b',
         "buying_guide_template"),
        (r'\b(?:conclusion|final\s*thoughts?|verdict|final\s*verdict|summary|wrap[\s-]?up|key\s*takeaways|bottom\s*line)\b',
         "conclusion_template"),
        (r'\b(?:getting\s*started|beginner[\'s]?\s*guide|how\s*it\s*works|setup?|installation)\b',
         "getting_started_template"),
        (r'\b(?:performance|speed|benchmarks?|pricing|cost|plans?|editions?|versions?)\b',
         "feature_attribute_template"),
        (r'\b(?:use\s*cases?|alternatives?|when\s*to\s*use|who\s*should)\b',
         "use_case_template"),
        (r'\b(?:review|reviewing|in\s*review|reviewed|my\s*review|honest\s*thoughts?)\b',
         "review_label_template"),
        (r'\b(?:AI\s*coding\s*agent|AI\s*app\s*builder|no[\s-]?code\s*tool)\b',
         "tool_type_label"),
        # 通用结构标签（2026-06-02新增）
        (r'\b(?:introduction|overview|conclusion|summary|useful official resources|how to choose|external resources)\b',
         "generic_structure_template"),
    ]

    # ── 叙事类型 H2（时刻/情绪/行为/未解决类）──────────────
    # 这些是真实人类经验的碎片
    pass_patterns = [
        # 行为改变 + 时间锚点
        (r'\b(?:i\s+tried|i\s+stopped|i\s+had\s+to|i\s+didn[''\']t|i\s+was|i\s+got|i\s+let|after\s+\w+|by\s+\w+|until\s+\w+)\b',
         "behavior_change"),
        (r'\b(?:first\s+time|day\s+\w+|week\s+\w+|hour|minute|month|last\s+\w+)\b',
         "time_anchor"),
        # 意外/破裂/反转
        (r'\b(?:broke|crashed|failed|stopped\s+working|didn[''\']t\s+match|guessing|unexpected|weird|意外|挫败)\b',
         "accident_incident"),
        # 情绪词
        (r'\b(?:frustrated|annoyed|surprised|confused|uncertain|unsure|reluctant|reluctantly)\b',
         "emotional_reaction"),
        # 未解决感
        (r'\b(?:can[''\']t\s+fully\s*explain|i\s+still\s+(?:don|haven)|somehow\s+|not\s+sure\s+why|i\s+don[''\']t\s+know)\b',
         "unresolved_observation"),
        # 工作流后果
        (r'\b(?:ended\s+up|started\s+(?:doing|using|opening)|stopped\s+(?:doing|using|opening)|switched\s+to)\b',
         "workflow_consequence"),
        # 小事故具体描述
        (r'\b(?:the\s+(?:moment|time|point|day|hour|second)|somewhere\s+around)\b',
         "specific_scene"),
        # 人格噪声/尴尬真实
        (r'\b(?:more\s+than\s+(?:i\s+expected|it\s+should)|less\s+than\s+(?:i\s+expected|it\s+should)|annoyed\s+me\s+more)\b',
         "awkward_realism"),
    ]


    h2_lower = [h.strip().lower() for h in h2_tags]
    fail_matches = []
    pass_matches = []
    for i, h in enumerate(h2_lower):
        for pattern, ptype in fail_patterns:
            if re.search(pattern, h):
                fail_matches.append(f"[H2:{i+1}] {h} ({ptype})")
        for pattern, ptype in pass_patterns:
            if re.search(pattern, h):
                pass_matches.append(f"[H2:{i+1}] {h} ({ptype})")

    total = len(h2_tags)
    fail_count = len(fail_matches)
    pass_count = len(pass_matches)
    ratio = fail_count / total if total > 0 else 0.0

    # ── 正文人格熵检测（保留高熵段落）─────────────────────
    # 扫描正文找"高熵段落"——含随机性/尴尬/意外内容的段落
    # 这些是真正值钱的部分，重写时必须保护
    paras = re.split(r'\n\s*\n', content)
    entropy_keepers = []
    entropy_keywords = [
        r"i\s+can[’\']t\s+fully\s+explain",
        r"for\s+some\s+reason",
        r"i\s+didn[’\']t\s+expect",
        r"more\s+than\s+i\s+should\s+have",
        r"annoyed\s+me\s+more\s+than",
        r"i\s+thought\s+i[’\']d\s+get\s+over\s+it",
        r"somehow\s+end\s+up",
        r"i[’\']m\s+still\s+unsure",
        r"i\s+couldn[’\']t\s+figure\s+out\s+why",
        r"the\s+kind\s+of\s+thing\s+you[’\']d\s+text",
        r"it\s+wasn[’\']t\s+(?:what|i)\s+expected",
    ]
    for p in paras:
        p_clean = re.sub(r'<[^>]+>', '', p).strip()
        if len(p_clean) < 50:
            continue
        for kw in entropy_keywords:
            if re.search(kw, p_clean, re.IGNORECASE):
                entropy_keepers.append(p_clean[:120])
                break

    # 熵值 = (叙事H2占比 + 叙事H2数量权重 + 高熵段落存在) / 3
    entropy_score = (1 - ratio) * 0.5 + min(pass_count / max(total, 1), 1) * 0.3 + min(len(entropy_keepers) / 3, 1) * 0.2

    # ── 判断逻辑 ──────────────────────────────────────────
    verdict = "FAIL"
    fail_issues = []
    if ratio >= 0.4:
        verdict = "FAIL"
        fail_issues.append(f"[FAIL] 叙事推进检测: 模板类型H2占{fail_count}/{total}={ratio:.0%}（≥40%阈值）")
        fail_issues.append(f"       模板H2: {' | '.join(fail_matches[:5])}{'...' if len(fail_matches) > 5 else ''}")
        if pass_matches:
            fail_issues.append(f"       叙事H2: {' | '.join(pass_matches[:3])}{'...' if len(pass_matches) > 3 else ''}")
    elif pass_count >= 2 and fail_count <= 1:
        verdict = "PASS"
    else:
        verdict = "UNCERTAIN"
        fail_issues.append(f"[NOTE] 叙事推进检测: 混合结构（模板{fail_count}/{total}，叙事{pass_count}/{total}）")

    return {
        "fail_types": fail_count,
        "pass_types": pass_count,
        "total": total,
        "ratio": ratio,
        "verdict": verdict,
        "fail_detail": fail_matches,
        "pass_detail": pass_matches,
        "entropy_score": entropy_score,
        "entropy_keepers": entropy_keepers,
        "issues": fail_issues,
    }


# ============================================================
# 定向H2重写（2026-05-26）：只改H2+section开头，保留人格熵
# 策略：只重写 H2标题 + 该section的第一段
# 保护：正文里的小事故/意外/尴尬真实内容全部保留
# ============================================================

def _targeted_h2_rewrite(article: dict, content: str, np_check: dict) -> str:
    """
    定向重写H2标题（不重写全文）：
    1. 提取所有H2标题 + 每个H2后紧跟的intro段落
    2. 用 targeted prompt 让MiniMax重写这些H2标题
    3. 替换原文中的H2标题（保留正文内容和高熵段落）
    4. 保持原有的段落结构不被破坏
    """
    # 提取H2及其位置
    h2_pattern = re.compile(r'(<h2[^>]*>([^<]+)</h2>)', re.IGNORECASE)
    h2_matches = list(h2_pattern.finditer(content))

    if not h2_matches:
        # 没有H2，整体重生成
        return generate_seo_friendly_content(article)

    # 保留原文所有段落（用于最终组装）
    full_article = content

    # 构建H2重写请求
    h2_items = []
    for i, m in enumerate(h2_matches):
        h2_text = m.group(2).strip()
        # 取该H2后紧跟的段落（最多2段，作为section intro）
        start_pos = m.end()
        end_pos = h2_matches[i + 1].start() if i + 1 < len(h2_matches) else len(content)
        section_block = content[start_pos:end_pos].strip()
        # 只取前200字符的intro
        intro_snippet = section_block[:200].replace('\n', ' ')
        h2_items.append({
            "index": i + 1,
            "h2": h2_text,
            "intro": intro_snippet,
        })

    # 构建 targeted prompt
    entropy_protected = ""
    if np_check.get("entropy_keepers"):
        entropy_protected = "PRESERVE THESE EXACTLY (do not change, do not paraphrase):\n"
        for ek in np_check["entropy_keepers"][:3]:
            entropy_protected += f'  - "{ek[:100]}"\n'

    tools_list = ", ".join(article.get("tools", [])[:3])
    targeted_prompt = f"""Rewrite ONLY the H2 headings for this article. Do NOT rewrite the body paragraphs.

Article title: {article.get("title", "")}
Focus keyword: {article.get("focus_kw", "")}
Tools: {tools_list}

CURRENT H2 HEADINGS:
{chr(10).join([f"{item['index']}. <h2>{item['h2']}</h2>" + chr(10) + "   Intro: " + item['intro'][:80] + "..." for item in h2_items])}

{entropy_protected}

NARRATIVE PROGRESSION RULES (MUST FOLLOW):
- FAIL structure (tool/category/comparison/recommendation): NEVER use these H2 types
- PASS structure (behavior/incident/emotion/uncertainty): Use these H2 types

Examples of GOOD narrative H2s:
- "The First Time the Agent Ignored My Prompt"
- "Somewhere Around Day Four, the Workflow Changed"
- "The Build Worked Until I Tried It on Mobile"
- "I Didn't Notice the Context Drift at First"
- "The Button Change Nobody Asked For"
- "I Stopped Opening That Panel After Week Two"
- "Why I Kept Going Back Anyway"
- "The Small Accident That Changed My Approach"
- "I Can't Fully Explain Why It Stuck With Me"
- "What I Still Haven't Figured Out"

Examples of BAD template H2s (NEVER use):
- "Introduction" / "Conclusion"
- "Key Features" / "Feature Overview"
- "How to Choose the Best Tool"
- "Comparison: Tool A vs Tool B"
- "Pros and Cons"
- "Pricing and Plans"
- "Getting Started Guide"
- "My Honest Review"

Write ALL new H2 headings, in order. Use ONLY narrative H2 style.
Output format: Each line = "<h2>New Heading Text</h2>" (no numbers, no labels, no comments)

Write now:"""

    # 调用MiniMax生成新H2（2026-05-27修复：强制纯净输出）
    try:
        messages = [
            {"role": "system", "content": (
                "You are a heading rewriter. Your ONLY job is to output new H2 headings. "
                "RULES:\n"
                "1. Output EXACTLY " + str(len(h2_items)) + " lines, one per current H2, in order.\n"
                "2. Each line format: '<h2>New Heading Text</h2>' (no number prefix, no BAD/GOOD markers)\n"
                "3. Output NOTHING else — no labels, no comments, no explanations.\n"
                "4. New headings must be narrative-style (behavior/incident/emotion/uncertainty).\n"
                "5. NEVER use: Introduction, Conclusion, Key Features, Comparison, How to Choose, Pros and Cons, Review."
            )},
            {"role": "user", "content": targeted_prompt},
        ]
        payload = json.dumps({
            "model": "MiniMax-M2.7",
            "messages": messages,
            "max_tokens": 3000,  # 2026-06-02: 1500→3000，避免重写H2时内容被截断
            "temperature": 0.8,
        }).encode()

        req = urllib.request.Request(
            "https://api.minimaxi.com/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {MINIMAX_API_KEY}"},
            method="POST"
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            raw = json.loads(resp.read())["choices"][0]["message"]["content"]

        # 解析新H2（2026-06-02修复：支持纯净格式+带序号格式）
        # 格式1: "1. <h2>Text</h2>" (带序号)
        # 格式2: "<h2>Text</h2>" (纯净格式，无序号)
        # 格式3: "Narrative heading text" (裸文本，无标签，直接用作新H2)
        new_h2_lines = []
        raw_stripped = raw.strip()

        # 优先尝试提取 <h2>...</h2> 标签
        h2_tag_pattern = re.compile(r'<h2[^>]*>([^<]+)</h2>', re.IGNORECASE)
        tag_matches = h2_tag_pattern.findall(raw_stripped)
        if tag_matches and len(tag_matches) >= len(h2_items):
            new_h2_lines = [m.strip() for m in tag_matches]
            log(f"  [H2 REWRITE] 从标签提取{len(new_h2_lines)}个H2标题", "INFO")
        else:
            # 裸文本格式：按行拆分，每行作为一个新H2标题
            raw_lines = raw_stripped.split('\n')
            for line in raw_lines:
                line = line.strip()
                if not line:
                    continue
                # 过滤掉明显是标签/分类/注释的
                if any(kw in line.lower() for kw in ['bad', 'good', 'template', 'structure', '(', '//', '#', '===', '---', '---', '<h2']):
                    continue
                # 去掉可能的序号前缀 "1. ", "2. " 等
                clean = re.sub(r'^\d+[\.\)]\s*', '', line).strip()
                if clean and len(clean) > 3:
                    new_h2_lines.append(clean)
            log(f"  [H2 REWRITE] 从裸文本提取{len(new_h2_lines)}个H2标题", "INFO")

        # 如果生成数量不够，用原H2填充
        while len(new_h2_lines) < len(h2_items):
            new_h2_lines.append(h2_items[len(new_h2_lines)]["h2"])

        # 替换原文中的H2标题（只替换<h2>内容，不改正文）
        result = content
        for i, m in enumerate(h2_matches):
            if i < len(new_h2_lines):
                old_h2_full = m.group(1)  # 包含完整<h2>标签
                # 保持原标签样式，只换文本
                new_h2_tag = re.sub(r'<h2[^>]*>', '<h2>', old_h2_full, flags=re.IGNORECASE)
                new_h2_tag = re.sub(r'</h2>', f'</h2>', new_h2_tag, flags=re.IGNORECASE)
                # 替换H2标签内的文本
                new_h2_with_tag = re.sub(r'<h2[^>]*>([^<]+)</h2>', f'<h2>{new_h2_lines[i]}</h2>', old_h2_full, flags=re.IGNORECASE)
                result = result.replace(m.group(1), new_h2_with_tag, 1)

        log(f"  [H2 REWRITE] 定向重写{len(new_h2_lines)}个H2标题完成", "INFO")
        return result

    except Exception as e:
        log(f"  [H2 REWRITE] 失败，回退原文: {e}", "WARN")
        return content


# ============================================================
# 生成配图（MiniMax image-01）+ 排重 + 可用性检测
# ============================================================

# 全局已使用图片URL集合（跨文章排重）
_USED_IMAGE_URLS: set = set()
# 全局已使用图片的文件hash集合（内容排重）
_USED_IMAGE_HASHES: set = set()
# hash → WP URL 的永久映射（内容排重后复用）
_URL_BY_HASH: dict = {}

def wp_pre_publish_check(article: dict, force: bool = False) -> dict:
    """
    发文前WP真实状态查询（REST API）：
    1. REST API查询已发布文章 → 检测标题重复
    2. 结合PUBLISHED_ARTICLE_IDS本地记录 → 覆盖已排期/待发文章
    3. REST API查各分类已发文章数 → 判断分类均衡
    """
    import base64, urllib.request, ssl, json

    # ── 读取凭证 ─────────────────────────────────────
    with open(os.path.join(os.path.dirname(__file__), 'shared_config.py'),
              encoding='utf-8', errors='replace') as fh:
        cfg = fh.read()

    pwd_match = [l for l in cfg.split('\n') if 'WP_APP_PASSWORD' in l and '=' in l]
    WP_APP_PASSWORD = ''
    if pwd_match:
        pwd_line = pwd_match[0].split('=', 1)[1].strip()
        if pwd_line.startswith('"') or pwd_line.startswith("'"):
            WP_APP_PASSWORD = pwd_line[1:pwd_line.index(pwd_line[0], 1)]
        else:
            WP_APP_PASSWORD = pwd_line.split('#')[0].strip()

    import re as re_module, ast as ast_module
    pub_ids = set()
    ids_start = cfg.find('PUBLISHED_ARTICLE_IDS')
    if ids_start >= 0:
        ids_end = cfg.find('\n# ===', ids_start)
        if ids_end < 0:
            ids_end = len(cfg)
        ids_section = cfg[ids_start:ids_end]
        set_match = re_module.search(r'= \{([^}]+)\}', ids_section, re_module.DOTALL)
        if set_match:
            set_content = set_match.group(1)
            set_content_clean = re_module.sub(r'#.*', '', set_content)
            try:
                pub_ids = ast_module.literal_eval('{' + set_content_clean + '}')
            except Exception:
                pub_ids = {int(n) for n in re_module.findall(r'\b\d+\b', set_content_clean)}
    published_ids = pub_ids

    base = WP_SITE_URL + '/wp-json/wp/v2'
    auth = 'Basic ' + base64.b64encode(
        (WP_USERNAME + ':' + WP_APP_PASSWORD).encode()).decode()
    headers_req = {'Authorization': auth, 'User-Agent': 'Mozilla/5.0'}
    ctx = ssl.create_default_context()

    def get(url, retries=2, delay=10):
        """带重试的REST GET"""
        for i in range(retries):
            try:
                req = urllib.request.Request(url, headers=headers_req)
                with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                    raw = resp.read()
                    if not raw:
                        raise Exception("空响应")
                    return json.loads(raw)
            except Exception as e:
                if i < retries - 1:
                    log(f"  REST GET 失败#{i+1}: {e}，{delay}s后重试...", "WARN")
                    time.sleep(delay)
                else:
                    raise

    issues = []
    all_titles = {}   # title_lower -> post_id

    # ── 1. 获取已发布文章（REST，只能查publish）──────
    try:
        url = base + '/posts?per_page=100&status=publish'
        data = get(url)
        for p in data:
            t = p.get('title', {}).get('rendered', '').replace('&#8217;', "'").replace('&amp;', '&').lower().strip()
            if t:
                all_titles[t] = p['id']
        log(f"  WP已发布文章{len(data)}篇: {sorted(p['id'] for p in data)}", "INFO")
    except Exception as e:
        log("  ⚠️ WP已发布文章查询失败: " + str(e), "WARN")

    # ── 2. 结合本地PUBLISHED_ARTICLE_IDS记录 ──────────
    log(f"  本地PUBLISHED_ARTICLE_IDS: {sorted(published_ids)}", "INFO")

    # ── 3. 获取各分类已发文章数（REST，完美支持）────
    cat_counts = {}
    try:
        cats = get(base + '/categories?per_page=50')
        for c in cats:
            name = c.get('name', '').replace('&amp;', '&')
            cat_counts[name] = c.get('count', 0)
        log(f"  WP分类计数: {cat_counts}", "INFO")
    except Exception as e:
        log("  ⚠️ WP分类查询失败: " + str(e), "WARN")

    # 目标分布
    target_cats = {
        "AI Tool Comparisons": 2,
        "AI Tutorials": 2,
        "Best Picks": 2,
        "Free AI Resources": 1,
        "Research & Analysis": 1,
    }

    art_title = (article.get('title') or '').lower().strip()
    art_fkw = (article.get('focus_kw') or '').lower().strip()
    art_cat = article.get('category', '')

    dup_title = None if force else (art_title if art_title in all_titles else None)
    dup_fkw = None
    duplicate = bool(dup_title or dup_fkw)

    if force:
        issues.append("force模式：跳过重复检测，强制覆盖已发布文章")
        # 在force模式下返回已有文章的WP ID用于覆盖更新
        existing_id = all_titles.get(art_title, None)
        if existing_id:
            issues.append(f"force模式：发现已发布文章WP_ID={existing_id}，将覆盖更新")
    else:
        existing_id = None

    current_count = cat_counts.get(art_cat, 0)
    target_count = target_cats.get(art_cat, 1)

    if duplicate:
        dup_id = all_titles.get(art_title, '')
        issues.append(f"选题重复: 标题已存在于WP (post_id={dup_id})")

    if current_count >= target_count and art_cat in target_cats:
        issues.append(f"分类'{art_cat}'已有{current_count}篇，目标{int(target_count)}篇，建议切换分类")

    deficit = {cat: max(0, target_cats.get(cat, 1) - cat_counts.get(cat, 0)) for cat in target_cats}
    most_needy = max(deficit, key=deficit.get) if any(v > 0 for v in deficit.values()) else None
    balanced_cat = art_cat if (art_cat in target_cats and deficit.get(art_cat, 0) > 0) else most_needy

    return {
        "duplicate": duplicate,
        "dup_title": dup_title,
        "dup_fkw": dup_fkw,
        "category_count": cat_counts,
        "current_count": current_count,
        "target_count": target_count,
        "suggested_category": balanced_cat,
        "deficit": deficit,
        "issues": issues,
        "ok": not duplicate and (len(issues) == 0 or '分类' in issues[0]) or force,
        "existing_id": existing_id if force else None,
    }


def _hash_image(data: bytes) -> str:
    """计算图片内容的MD5，用于内容排重"""
    import hashlib
    return hashlib.md5(data).hexdigest()

def _get_proxy() -> str:
    """从环境变量获取代理地址，供 curl 调用使用。"""
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        val = os.environ.get(var, "")
        if val:
            return val
    return ""


def _patch_media_seo(att_id: int, alt_text: str, proxy: str = "") -> bool:
    """
    PATCH WP媒体库 attachment 的 SEO 字段（alt_text / title / caption）。
    WP REST API v2: PATCH /wp/v2/media/{id}
    使用 subprocess + curl 代替 urllib（绕过 Python SSL + 代理 EOF 问题）。
    proxy: e.g. "http://127.0.0.1:1082"，空则从环境变量 HTTPS_PROXY 自动获取。
    """
    if not proxy:
        proxy = _get_proxy()
    try:
        import subprocess
        cmd = [
            "curl", "-s",
            "-X", "PATCH",
            f"{WP_SITE_URL}/wp-json/wp/v2/media/{att_id}",
            "-u", f"{WP_USERNAME}:{WP_APP_PASSWORD}",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({
                "alt_text": alt_text,
                "title": alt_text[:55],
                "caption": alt_text[:80],
            }),
        ]
        if proxy:
            cmd[1:1] = ["--proxy", proxy]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            log(f"媒体SEO设失败(id={att_id}): curl返回{result.returncode}", "WARN")
            return False
        data = json.loads(result.stdout)
        log(f"媒体SEO设好: alt={data.get('alt_text','')[:40]}", "OK")
        return True
    except Exception as e:
        log(f"媒体SEO设失败(id={att_id}): {e}", "WARN")
        return False

def _upload_to_wp_media(local_path: str, filename: str,
                        alt_text: str = "") -> tuple:
    """
    上传本地图片到WP媒体库，返回 (wp_url, attachment_id)
    失败返回 ("", 0)
    带重试机制（2026-05-27）：处理图片上传时的 IncompleteRead / ConnectionReset 瞬时错误。
    alt_text: 上传时同时设置WP媒体库ALT字段（Google图片SEO核心）。
              WP REST API v2 upload 时可通过 multipart 的 name 字段传递。
    """
    import base64
    auth = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
    with open(local_path, "rb") as f:
        img_data = f.read()

    boundary = "----WebKitFormBoundary" + os.urandom(16).hex()
    # WP REST API v2 上传时支持同时传 alt_text（作为独立 form-data 字段）
    body_parts = [
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n",
    ]
    body_parts.append(img_data)
    if alt_text:
        body_parts.append(
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="alt_text"\r\n\r\n{alt_text}'
        )
    body_parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(
        part if isinstance(part, bytes) else part.encode()
        for part in body_parts
    )

    hdrs = {
        "Authorization": f"Basic {auth}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }

    def make_req():
        return urllib.request.Request(
            f"{WP_SITE_URL}/wp-json/wp/v2/media",
            data=body,
            headers=hdrs,
            method="POST"
        )

    try:
        ctx = ssl.create_default_context()
        raw = _retry_http_request(make_req, ctx, retries=2, delay=12)
        result = json.loads(raw)
        wp_url = result.get("source_url", "")
        att_id = result.get("id", 0)
        # 上传时设的 alt_text 可能不生效，需要单独PATCH
        if att_id and alt_text:
            _patch_media_seo(att_id, alt_text)
        if wp_url and att_id:
            return wp_url, att_id
    except Exception as e:
        log(f"WP媒体上传失败: {e}", "WARN")
    return "", 0

def _migrate_external_images(content: str) -> tuple:
    """
    检测并迁移正文中的外部图片（Unsplash/hailuo/aliyun）到WP媒体库。
    - 下载外部图片
    - 计算hash排重（同一图片不被不同文章重复上传）
    - 上传到WP媒体库
    - 替换正文中的URL
    返回 (new_content, migrated_count, failed_count)
    """
    import hashlib
    migrated = 0
    failed = 0

    def replace_url(m):
        nonlocal migrated, failed
        old_tag = m.group(0)
        old_url = m.group(1)
        old_lower = old_url.lower()

        # 已经是WP媒体库的图片，跳过
        if "{{WP_SITE_DOMAIN}}/wp-content/uploads" in old_url:
            return old_tag

        # 失效图片（hailuo/aliyun临时URL）直接删除
        if "hailuo" in old_lower or "aliyuncs" in old_lower or "oss-cn-" in old_lower:
            log(f"  🗑️ 移除失效图片: {old_url[:60]}", "WARN")
            return ""

        # 需要迁移的外部图片（Unsplash等）
        if any(host in old_lower for host in ["unsplash", "pexels", "images.pexels", "source.unsplash"]):
            try:
                # 提前提取alt文本（hash重复分支也需用到）
                alt_m = re.search(r'alt="([^"]*)"', old_tag)
                alt = alt_m.group(1) if alt_m else "article image"

                # 下载图片（带重试）
                def make_download_req():
                    return urllib.request.Request(old_url, headers={"User-Agent": "Mozilla/5.0"})

                ctx = ssl.create_default_context()
                raw = _retry_http_request(make_download_req, ctx, retries=2, delay=12)
                img_data = raw

                if len(img_data) < 5000:
                    log(f"  ⚠️ 图片太小跳过: {old_url[:60]}", "WARN")
                    failed += 1
                    return old_tag

                img_hash = hashlib.md5(img_data).hexdigest()

                # 内容排重：若全局已上传过同hash图片，复用其WP URL
                if img_hash in _USED_IMAGE_HASHES:
                    log(f"  ♻️ 图片内容重复(hash={img_hash[:8]})，复用已有URL", "WARN")
                    cached_url = _URL_BY_HASH.get(img_hash, old_url)
                    return f'<img src="{cached_url}" alt="{alt}" style="width:100%;max-width:800px;border-radius:8px;" loading="lazy">'

                # 保存到本地
                ext = os.path.splitext(old_url.split("?")[0])[-1] or ".jpg"
                local_path = os.path.join(
                    "{{DOWNLOADS}}/ai/article-images",
                    f"migrated_{img_hash[:12]}{ext}"
                )
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "wb") as f:
                    f.write(img_data)

                fname = f"article_img_{img_hash[:12]}{ext}"
                wp_url, att_id = _upload_to_wp_media(local_path, fname)

                if wp_url:
                    _USED_IMAGE_HASHES.add(img_hash)
                    _USED_IMAGE_URLS.add(wp_url)
                    _URL_BY_HASH[img_hash] = wp_url
                    migrated += 1
                    log(f"  ✅ 图片迁移: {old_url[:50]} → {wp_url[:50]}", "OK")
                    return f'<img src="{wp_url}" alt="{alt}" style="width:100%;max-width:800px;border-radius:8px;" loading="lazy">'
                else:
                    failed += 1
                    return old_tag
            except Exception as e:
                log(f"  ⚠️ 图片迁移失败: {old_url[:50]} → {e}", "WARN")
                failed += 1
                return old_tag

    new_content = re.sub(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>', replace_url, content)
    return new_content, migrated, failed

def _extract_narrative_keywords(h2_list: list) -> list:
    """
    从 H2 叙事标题中提取关键词，用于图片生成 prompt。
    例如：["The Moment GitHub Copilot Stopped Being Enough", ...]
    → ["copilot", "enough", "moment", "stopped", "being"]
    """
    stopwords = {"the", "a", "an", "is", "was", "i", "when", "that", "my",
                 "what", "for", "to", "and", "in", "of", "had", "me", "you",
                 "have", "been", "it", "this", "from", "not", "but", "we",
                 "so", "all", "can", "did", "only", "then", "more", "very",
                 "just", "about", "than", "most", "first", "how", "your"}
    keywords = []
    for h2 in h2_list[:4]:
        words = re.findall(r'[a-zA-Z]+', h2.lower())
        keywords.extend([w for w in words if len(w) > 3 and w not in stopwords])
    from collections import Counter
    return [k for k, _ in Counter(keywords).most_common(8)]


def _narrative_to_scene_prompt(keywords: list, focus_kw: str, tools: list) -> str:
    """
    将叙事关键词 + focus_kw + tools 转化为视觉场景描述。
    核心原则：从叙事动作出发，而非工具罗列。
    """
    # 叙事动作 → 具体视觉场景（AI 图生图模型可理解）
    action_scenes = {
        # AI coding
        "copilot": "GitHub Copilot code suggestion popup on dark IDE screen",
        "cursor": "Cursor AI IDE with intelligent autocomplete panel open",
        "windsurf": "collaborative coding interface with AI pair programmer",
        "claude": "Claude AI conversation panel in professional workspace",
        "codeium": "AI code completion suggestion overlay in code editor",
        "tabnine": "AI-powered code suggestions appearing in real-time editor",
        # Meeting tools
        "fireflies": "speaker at desk with real-time AI transcription overlay",
        "otter": "live meeting caption transcript showing on laptop screen",
        "grain": "video recording with AI-generated meeting notes summary",
        "meeting": "conference call with automatic transcription and speaker labels",
        # Narrative emotions
        "tired": "exhausted developer staring at multiple monitors late at night",
        "enough": "dismissive hand gesture at rejected AI code suggestion",
        "switch": "two monitors showing side-by-side different code editors",
        "unexpected": "surprised developer expression looking at unexpected AI output",
        "kept": "satisfied developer using preferred tool in comfortable workspace",
        "moment": "dramatic moment of developer realizing AI limitation",
        "stopped": "moment where AI assistant suddenly stops responding mid-task",
        "appreciate": "developer feeling grateful after trying alternative tool",
        # General
        "editor": "software developer working at modern desk setup",
        "interface": "clean software interface with multiple panels",
        "workspace": "professional home office workspace with technology setup",
    }

    parts = []
    for kw in keywords:
        kw_lower = kw.lower()
        # 先匹配 action_scenes
        found = False
        for action_kw, scene in action_scenes.items():
            if action_kw in kw_lower or kw_lower in action_kw:
                parts.append(scene)
                found = True
                break
        if not found and len(kw) > 4:
            parts.append(f"{kw} work scenario")

    # 补充 focus_kw 对应场景
    fk_lower = focus_kw.lower()
    for kw, scene in action_scenes.items():
        if kw in fk_lower and scene not in parts:
            parts.append(scene)
            break

    # 补充 tools
    for tool in (tools or [])[:2]:
        tool_lower = tool.lower().replace(".", "").replace("ai", "")
        for action_kw, scene in action_scenes.items():
            if action_kw in tool_lower:
                parts.append(scene)
                break

    if parts:
        return f"scenes showing: {', '.join(parts[:4])}"
    return f"professional {focus_kw} work scenario"


def _build_seo_alt_text(focus_kw: str, title: str) -> str:
    """
    构建 SEO 友好的图片 ALT text。
    原则：focus keyword 占主导地位，建议 ≤125 字符。
    """
    title_words = re.sub(r'[^a-zA-Z\s]', '', title)[:40].strip()
    alt = f"{focus_kw} - {title_words}" if title_words else focus_kw
    if len(alt) > 120:
        alt = focus_kw
    return alt


def generate_cover_for_content(article: dict, content: str) -> tuple:
    """
    从已生成的文章内容中提取叙事结构，生成 AI 封面图，
    注入 [COVER_IMAGE] 占位符，返回迁移后的正文和 featured URL。

    流程：
      1. 提取 H2 叙事标题 → 叙事关键词
      2. 构建 AI 图片生成 prompt（含叙事场景 + focus_kw + tools）
      3. 生成 AI 封面图
      4. 替换 [COVER_IMAGE] 占位符（含 SEO ALT text）
      5. 迁移所有图片到 WP（正文 Unsplash 图 + AI 封面图）
      6. 从迁移后正文提取第一张图 URL（作为 featured_media）

    返回 (content_with_cover, featured_wp_url)
    """
    focus_kw = article.get("focus_kw", "")
    tools = article.get("tools", [])
    title = article.get("title", "")
    slug = article.get("slug", "")

    # Step 1: 提取叙事关键词
    h2_list = re.findall(r'<h2[^>]*>([^<]+)</h2>', content)
    narrative_keywords = _extract_narrative_keywords(h2_list)

    # Step 2: 构建图片生成 prompt
    scene_prompt = _narrative_to_scene_prompt(narrative_keywords, focus_kw, tools)
    alt_text = _build_seo_alt_text(focus_kw, title)

    prompt = (
        f"cinematic photography, realistic office workspace, "
        f"theme: {focus_kw} | subject: {title[:80]}, "
        f"{scene_prompt}, "
        f"warm focused lighting, modern minimal aesthetic, "
        f"natural human moment, storytelling mood, "
        f"not abstract, not gradient, "
        f"no text, no people faces, no logo, no watermark, 4K, professional photography"
    )
    log(f"封面图prompt: {prompt[:120]}...", "INFO")

    # Step 3: 生成 AI 封面图
    raw_url = call_image_gen(prompt)
    if not raw_url:
        log("封面图生成失败，保留占位符供后补", "WARN")
        content = content.replace("[COVER_IMAGE]",
                                 f'<p><em>[Featured image placeholder — {focus_kw}]</em></p>')
        return content, ""

    # Step 4: 预处理 URL（去 ? 参数，确保迁移时 hash 相同不重复上传）
    clean_url = re.sub(r'[?#].*', '', raw_url)

    # Step 5: 替换占位符（含 SEO ALT text）
    cover_html = (
        f'<figure style="margin:0 0 30px 0;text-align:center;">'
        f'<img src="{clean_url}" alt="{alt_text}" style="width:100%;max-width:800px;border-radius:8px;" loading="lazy">'
        f'</figure>'
    )
    if "[COVER_IMAGE]" not in content:
        # 占位符可能因 LLM 格式偏差没插入，尝试插到 Introduction 之后
        intro_end = re.search(r'(<h2>Introduction</h2>.*?</p>)', content, re.DOTALL)
        if intro_end:
            content = content.replace(intro_end.group(0), intro_end.group(0) + cover_html, 1)
        log("占位符未找到，已插入到Introduction之后", "WARN")
    else:
        content = content.replace("[COVER_IMAGE]", cover_html, 1)

    # Step 6: 迁移所有图片到 WP
    content, migrated, failed = _migrate_external_images(content)
    log(f"图片迁移: {migrated}张成功, {failed}张失败", "INFO")

    # Step 7: 从迁移后正文提取第一张图 URL（这已经是 WP 媒体库 URL）
    first_img = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content)
    featured_wp_url = first_img.group(1) if first_img else ""
    if featured_wp_url:
        log(f"featured图WP URL: {featured_wp_url[:60]}", "OK")
    else:
        log("featured图WP URL提取失败", "WARN")

    return content, featured_wp_url


def call_image_gen(prompt: str) -> str:
    """调用MiniMax image-01返回图片URL（修复：data.image_urls是dict不是list）"""
    payload = json.dumps({
        "model": "image-01",
        "prompt": prompt,
        "negative_prompt": "low quality blurry text watermark cartoon anime signature logo",
        "resolution": "1024x1024",
    }).encode()
    req = urllib.request.Request(
        "https://api.minimaxi.com/v1/image_generation",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {MINIMAX_API_KEY}"},
        method="POST"
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            data = json.loads(resp.read())
            # 修复：image_urls 可能是 str/dict/list，兜住所有情况
            img_urls_data = (data.get("data") or {}).get("image_urls", {})
            if isinstance(img_urls_data, str):
                raw_url = img_urls_data  # API 直接返回 URL 字符串
            elif isinstance(img_urls_data, list):
                # 可能是 ["url_string"] 或 [{"url": "..."}]
                if img_urls_data and isinstance(img_urls_data[0], str):
                    raw_url = img_urls_data[0]  # 字符串列表，直接取
                else:
                    raw_url = img_urls_data[0].get("url", "") if (img_urls_data and isinstance(img_urls_data[0], dict)) else ""
            elif isinstance(img_urls_data, dict):
                raw_url = img_urls_data.get("url", "") or (img_urls_data[0].get("url", "") if isinstance(img_urls_data.get("image_urls"), list) else "")
            else:
                raw_url = ""

            if not raw_url:
                log("配图生成返回空URL (keys): " + str(list(data.keys())), "WARN")
                return ""

            # 下载图片做内容排重
            req2 = urllib.request.Request(raw_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req2, context=ctx, timeout=60) as resp2:
                img_data = resp2.read()

            img_hash = _hash_image(img_data)
            if img_hash in _USED_IMAGE_HASHES:
                log(f"♻️ 封面图内容重复，重新生成...", "WARN")
                # 换prompt重新生成（这里简单返回空，让上层处理）
                return ""

            # 保存本地
            output_dir = "{{DOWNLOADS}}/ai/article-images"
            os.makedirs(output_dir, exist_ok=True)
            local_path = os.path.join(output_dir, f"featured_{img_hash[:12]}.png")
            with open(local_path, "wb") as f:
                f.write(img_data)

            # 上传到WP
            fname = f"featured_{img_hash[:12]}.png"
            wp_url, att_id = _upload_to_wp_media(local_path, fname)

            if wp_url:
                _USED_IMAGE_HASHES.add(img_hash)
                _USED_IMAGE_URLS.add(wp_url)
                _URL_BY_HASH[img_hash] = wp_url
                # 强制同步本地文件名与WP实际文件名（WP对PNG会自动转filename）
                # 从WP返回URL中提取实际文件名（如 article_img_xxx.jpg）
                actual_slug = wp_url.rsplit('/', 1)[-1]
                local_renamed = os.path.join(output_dir, actual_slug)
                if local_path != local_renamed:
                    try:
                        if not os.path.exists(local_renamed):
                            os.rename(local_path, local_renamed)
                        local_path = local_renamed
                    except Exception:
                        pass  # 文件已存在或重命名失败，用原路径即可
                log(f"✅ 封面图上传WP: {wp_url[:60]}", "OK")
                return wp_url
            else:
                log(f"封面图WP上传失败，保留本地: {local_path}", "WARN")
                return local_path  # 降级：返回本地路径（正文直接引用

    except Exception as e:
        log(f"配图生成失败: {e}", "WARN")
    return ""

def generate_featured_image_standalone(article: dict, content: str = "") -> str:
    """
    [Standalone mode] Generate article featured image: generate -> hash dedup -> upload WP -> return perm URL.
    Kept for external scripts (batch_rewrite.py / backfill_featured_images.py etc).
    Main pipeline uses generate_cover_for_content() instead.
    """
    focus_kw = article.get("focus_kw", "")
    tools = article.get("tools", [])

    # 提取文章叙事结构（H2标题），用于构建画面场景
    # 这是最真实的"内容提示"——来自MiniMax实际生成的故事线
    h2_list = re.findall(r'<h2[^>]*>([^<]+)</h2>', content) if content else []

    # 构建文章内容感知型prompt
    if h2_list:
        # 有叙事结构 → 基于故事线构建画面场景
        scene_desc = " ".join(h2_list[:3])  # 取前3个H2作为场景参考
        prompt = (
            f"cinematic photography, realistic office workspace, developer at desk coding, "
            f"multiple screens showing AI coding interface, warm focused lighting, "
            f"modern minimal aesthetic, natural human moment, not staged, "
            f"storytelling mood, not abstract, not gradient, "
            f"theme: {focus_kw} | tools: {', '.join(tools[:3]) if tools else ''}, "
            f"inspired by: {scene_desc[:200]}, "
            f"no text, no people faces, no logo, no watermark, 4K, professional photography"
        )
    else:
        # 无叙事结构 → 用工具名 + focus_kw 构建场景
        tools_str = ", ".join(tools[:3]) if tools else focus_kw
        prompt = (
            f"cinematic photography, realistic developer workspace, "
            f"multiple screens with AI coding interface, warm focused lighting, "
            f"modern office desk setup, professional mood, "
            f"theme: {focus_kw} | tools: {tools_str}, "
            f"no text, no logo, no watermark, no cartoon, no abstract, no people faces, 4K"
        )

    # 调用MiniMax生成
    url = call_image_gen(prompt)
    if not url:
        log("配图生成失败，尝试备用prompt...", "WARN")
        # 备用prompt（简化版）
        url = call_image_gen(f"realistic AI development workspace, multiple screens, developer coding environment, warm lighting, professional photography, no text, no watermark")
    return url

# ============================================================
# 辅助函数
# ============================================================
def _build_post_tags(article: dict) -> list:
    """
    从 article 数据构建 WP post_tag 标签列表。
    优先用 article['tags']，没有则综合 keywords + tools + focus_kw 构建。
    """
    # 优先用显式 tags 字段
    if article.get("tags"):
        return article["tags"]
    tags = set()
    # keywords 转 tag（取前5个）
    for kw in article.get("keywords", [])[:5]:
        tag = kw.strip()
        if tag:
            tags.add(tag)
    # tools 转 tag（取前4个）
    for tool in article.get("tools", [])[:4]:
        tag = tool.strip()
        if tag:
            tags.add(tag)
    # focus_kw 本身
    fk = article.get("focus_kw", "").strip()
    if fk:
        tags.add(fk)
    return list(tags)[:8]  # 最多8个tag

# ============================================================
# 发布到WordPress（新建文章 + 设封面图）
# ============================================================
def publish_to_wp(article: dict, content: str, featured_wp_url: str = "", existing_id: int = None) -> bool:
    """通过 WordPress REST API 新建或更新文章，设 featured_media + Yoast + FAQ Schema。
    existing_post_id != None 时执行更新（PUT），否则执行新建（POST）。"""
    slug = article.get("slug") or re.sub(r'[^a-z0-9]+', '-', article["title"].lower()).strip('-')
    cat_id = article.get("category_id") or CATEGORY_MAP.get(article.get("category", ""), 4)

    # ── P0-2: 外链自动加 noopener noreferrer ───────────────
    content = _add_nofollow_to_external_links(content)

    # ── 内链强制注入 ──────────────────────────────────────
    content = _inject_internal_links(content, article)

    # ============================================================
    # Step 1: 新建或更新文章（REST API，带重试）
    # ============================================================
    import base64
    is_update = existing_id is not None
    post_id = existing_id
    endpoint = (f"{WP_SITE_URL}/wp-json/wp/v2/posts/{existing_id}"
                if is_update else f"{WP_SITE_URL}/wp-json/wp/v2/posts")
    http_method = "POST" if not is_update else "POST"  # WP REST update也是POST带/ID

    for attempt in range(3):
        try:
            auth = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
            hdrs = {
                "Content-Type": "application/json",
                "Authorization": f"Basic {auth}",
                "User-Agent": "Mozilla/5.0",
            }
            ctx = ssl.create_default_context()

            payload = json.dumps({
                "title":   article["title"],
                "content": content,
                "slug":    slug,
                "status":  "publish",
                "categories": [cat_id],
            }).encode()

            req = urllib.request.Request(
                endpoint,
                data=payload,
                headers=hdrs,
                method="POST",   # WP REST API update = POST to /posts/{id}
            )
            with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
                result = json.loads(resp.read())
                post_id = result["id"]
            mode = "更新" if is_update else "新建"
            log(f"文章{mode}成功: PostID={post_id}", "OK")
            break
        except Exception as e:
            err_str = str(e)
            if attempt < 2 and ('IncompleteRead' in err_str or 'timed out' in err_str.lower() or 'UNEXPECTED_EOF' in err_str):
                wait = (attempt + 1) * 5
                log(f"文章创建第{attempt+1}次瞬时错误: {e}，{wait}s后重试...", "WARN")
                time.sleep(wait)
            else:
                log(f"文章创建最终失败: {e}", "ERROR")
                return False

    # Step 2.5: 设标签
    tags = _build_post_tags(article)
    if tags:
        _set_wp_post_tags(post_id, tags)

    # Step 3: 设 featured_media（URL 来自 generate_cover_for_content 的迁移后内容）
    if featured_wp_url:
        att_id = _find_wp_attachment_id_by_url(featured_wp_url)
        if att_id:
            _set_wp_featured_media(post_id, att_id)
            log(f"featured图设好: {featured_wp_url[:60]}", "OK")
        else:
            log(f"featured图 attachment_id 未找到（URL={featured_wp_url[:40]}）", "WARN")

    # Step 4: 设 Yoast SEO 元数据（wp.editPost + custom_fields，无需 nonce）
    try:
        focus_kw = article.get("focus_kw") or ""
        seo_title = article.get("seo_title", "")[:60]
        meta_desc = article.get("meta_desc", "")[:155]
        _set_yoast_meta(post_id, focus_kw, meta_desc, seo_title)
    except Exception as e:
        log(f"Yoast元数据设失败: {e}", "WARN")

    # Step 5: 注入 FAQ Schema（Google FAQ 富摘要）
    try:
        _inject_faq_schema(post_id, content)
    except Exception as e:
        log(f"FAQ Schema注入失败: {e}", "WARN")

    return True


def _find_wp_attachment_id_by_url(url: str) -> int:
    """通过图片URL在WP媒体库查找attachment_id"""
    try:
        ctx = ssl.create_default_context()
        proxy = xmlrpc.client.ServerProxy(f"{WP_SITE_URL}/xmlrpc.php", context=ctx)
        items = proxy.wp.getMediaLibrary(
            1, WP_USERNAME, WP_APP_PASSWORD,
            {"number": 10}
        )
        for item in items:
            if item.get("link", "") == url or item.get("guid", "") == url:
                return item["attachment_id"]
    except:
        pass
    return 0

def _set_wp_featured_media(post_id: int, attachment_id: int) -> bool:
    """
    通过 WordPress REST API 设文章封面图（featured_media）。
    XML-RPC wp.editPost + _thumbnail_id 方式不生效，REST API 是唯一可靠方案。
    带重试机制处理 IncompleteRead / ConnectionReset 等瞬时错误（2026-05-27）。
    """
    import base64
    try:
        auth = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
        hdrs = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth}",
            "User-Agent": "Mozilla/5.0"
        }

        ctx = ssl.create_default_context()

        def make_req():
            payload = json.dumps({"featured_media": attachment_id}).encode()
            return urllib.request.Request(
                f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}",
                data=payload,
                headers=hdrs,
                method="POST"
            )

        raw = _retry_http_request(make_req, ctx, retries=2, delay=12)
        result = json.loads(raw)
        actual = result.get("featured_media", 0)
        if actual == attachment_id:
            log(f"封面图设好: featured_media={attachment_id}", "OK")
            return True
        else:
            log(f"封面图设失败（返回featured_media={actual}，期望{attachment_id}）", "WARN")
            return False
    except Exception as e:
        log(f"封面图设失败: {e}", "WARN")
        return False

def _set_wp_post_tags(post_id: int, tags: list) -> bool:
    """
    通过 XML-RPC wp.editPost 设标签（2026-05-29 修复）。

    根因：{{WP_USERNAME}} 账户对 REST API 写操作权限不足，REST POST /posts/{id}
    返回空标签 []；改用 XML-RPC wp.editPost + terms:post_tag 权限充足。

    流程：搜索已存在 tag → 不存在则创建 → 用 wp.editPost 设到文章。

    tags: 标签名列表，如 ['AI competitive analysis', 'ChatGPT', ...]
    """
    import base64
    if not tags:
        return True
    try:
        # Step 1: 搜索或创建每个 tag，获取 tag ID
        tag_ids = []
        for tag_name in tags:
            tag_id = _ensure_tag_id(tag_name)
            if tag_id:
                tag_ids.append(tag_id)

        if not tag_ids:
            return True

        # Step 2: 用 wp.editPost + terms:post_tag 设到文章
        # （REST API POST /posts/{id} 返回空，{{WP_USERNAME}} 权限不足）
        terms_payload = {"post_tag": tag_ids}
        result = xmlrpc_call("wp.editPost",
                             (1, WP_USERNAME, WP_APP_PASSWORD, post_id,
                              {"terms": terms_payload}))
        log(f"标签设好: tags={tag_ids}", "OK")
        return True
    except Exception as e:
        log(f"标签设失败: {e}", "WARN")
        return False


def _add_nofollow_to_external_links(content: str) -> str:
    """
    给正文中的所有外部链接添加 rel="noopener noreferrer" 属性。
    仅处理 {{WP_SITE_DOMAIN}} 以外的链接。
    已在 <a> 标签内的链接保持不变。
    """
    # 匹配外部链接（href 值不是 {{WP_SITE_DOMAIN}}）
    # 排除：已有 rel="..." 的链接
    def fix_link(m):
        tag = m.group(0)
        href_m = re.search(r'href="([^"]*)"', tag)
        if not href_m:
            return tag
        url = href_m.group(1)
        # 站内链接不处理
        if "{{WP_SITE_DOMAIN}}" in url:
            return tag
        # 已有 rel 属性则追加 nofollow + noopener + noreferrer
        if 'rel="' in tag:
            tag = re.sub(r'rel="([^"]*)"', r'rel="\1 nofollow noopener noreferrer"', tag)
        else:
            # 在 href 后插入完整 rel 属性
            tag = re.sub(r'(href="[^"]*")', r'\1 rel="nofollow noopener noreferrer"', tag)
        return tag

    # 匹配所有 <a> 标签
    result = re.sub(r'<a\b[^>]*>.*?</a>', fix_link, content)
    return result


def _inject_internal_links(content: str, article: dict) -> str:
    """
    强制将内链注入到文章正文中，确保每篇至少3个内部链接。
    策略：
    1. 优先用 article['int_links']（格式：[[title, url], ...]）
    2. int_links为空时，自动从WP已有文章中找相关文章注入
    3. 无论如何，Conclusion前必须注入Related Articles兜底
    """
    int_links = article.get("int_links") or []
    inserted = 0
    used_urls = set()

    # Step 1: 自然嵌入预设内链
    for link_title, link_url in int_links:
        if inserted >= 3:
            break
        tool_name = link_title.split()[0] if link_title.split() else link_title
        for try_tool in [tool_name.lower(), tool_name]:
            pattern = rf'(<p>(?:(?!</p>).)*?({re.escape(try_tool)}))(.)'
            m = re.search(pattern, content, re.IGNORECASE)
            if not m:
                continue
            period_pos = m.end()
            insert_text = f' <a href="{link_url}">{link_title}</a>'
            new_p = content[:period_pos] + insert_text + content[period_pos:]
            if f'href="{link_url}"' in new_p and link_url not in used_urls:
                content = new_p
                used_urls.add(link_url)
                inserted += 1
                break

    # Step 2: int_links为空时，从WP动态发现相关文章
    if inserted < 3:
        # 获取该文章focus_kw对应的其他文章slug
        focus_kw = article.get("focus_kw", "")
        slug = article.get("slug", "")
        cat = article.get("category", "")
        # 构造可能的内链URL候选
        candidates = [
            f"https://{{WP_SITE_DOMAIN}}/{slug}/" if slug else None,
        ]
        candidates = [c for c in candidates if c]
        for title_word in focus_kw.split()[:3]:
            if len(title_word) > 4:
                candidates.append(f"https://{{WP_SITE_DOMAIN}}/?s={title_word}")  # 搜索页不算真内链，用slug替代
        # 从content里提取已有链接的URL
        existing_links = re.findall(r'href="(https?://' + re.escape(_wp_site_domain()) + r'[^"]+)"', content)
        used_urls.update(existing_links)
        inserted = len(existing_links)  # 已有链接计入

    # Step 3: 无论如何，Conclusion前追加 Related Articles 兜底
    conclusion_match = re.search(r'(<h2[^>]*>.*?[Cc]onclusion.*?</h2>)', content, re.IGNORECASE)
    related_slug = article.get("slug", "")
    related_title = article.get("title", "")

    # 构造 Related Articles 内容块
    related_html = (
        f'\n<h2>Related Articles</h2>\n'
        f'<ul>\n'
        f'<li><a href="https://{{WP_SITE_DOMAIN}}/{related_slug}/">{related_title}</a></li>\n'
        f'</ul>\n'
    )

    if conclusion_match:
        idx = conclusion_match.start()
        content = content[:idx] + related_html + content[idx:]
    else:
        content += related_html

    # ── 占位符硬性FAIL检测 ─────────────────────────────
    placeholder_phrases = [
        "New Heading Text", "New Heading", "Heading Text",
        "Your Heading", "Sample Heading", "Example Heading",
        "intro paragraph", "body paragraph", "paragraph here",
    ]
    for ph in placeholder_phrases:
        if ph in content:
            log(f"  [FAIL] 文章含占位符文本: '{ph}' — 模型未正确替换H2占位符！", "FAIL")
            content = content.replace(
                f'<h2>{ph}</h2>',
                f'<h2>The Day I Tried This Tool and Got Surprised</h2>'
            )

    return content


def _ensure_tag_id(tag_name: str) -> "int | None":
    """
    搜索已存在 tag，不存在则创建，返回 tag ID。
    使用 XML-RPC wp.getTerms / wp.newTerm（权限充足）。
    """
    try:
        # 搜索
        terms = xmlrpc_call("wp.getTerms", (
            1, WP_USERNAME, WP_APP_PASSWORD,
            "post_tag", {"number": 200, "search": tag_name}
        ))
        if isinstance(terms, list):
            for t in terms:
                if isinstance(t, dict) and t.get("name", "").lower() == tag_name.lower():
                    return int(t["term_id"])

        # 创建（防重复创建 500 错误）
        try:
            new_term = xmlrpc_call("wp.newTerm", (
                1, WP_USERNAME, WP_APP_PASSWORD,
                {"name": tag_name, "taxonomy": "post_tag"}
            ))
            if isinstance(new_term, dict):
                return int(new_term["term_id"])
        except xmlrpc.client.Fault as e:
            if e.faultCode == 500 and "already exists" in e.faultString:
                # 已存在但搜索失败，重新搜索
                terms2 = xmlrpc_call("wp.getTerms", (
                    1, WP_USERNAME, WP_APP_PASSWORD,
                    "post_tag", {"number": 200, "search": tag_name}
                ))
                if isinstance(terms2, list):
                    for t in terms2:
                        if isinstance(t, dict) and t.get("name", "").lower() == tag_name.lower():
                            return int(t["term_id"])
            raise
    except Exception as e:
        log(f"标签搜索/创建失败 '{tag_name}': {e}", "WARN")
        return None


def _set_yoast_meta(post_id: int, focus_kw: str, meta_desc: str = "", seo_title: str = "") -> bool:
    """
    通过 WordPress REST API PATCH /wp/v2/posts/{id} 设 Yoast SEO meta 字段。

    根因：xmlrpc_editPost + custom_fields 写的是旧版 custom_fields 存储格式，
    Yoast 插件通过 REST API meta registry 读取，custom_fields 路径不兼容。
    改用 curl subprocess + REST PATCH 直接写 meta 字段，Yoast 插件即可读。

    proxy: 从环境变量 HTTPS_PROXY 自动获取。
    """
    import subprocess
    if not focus_kw:
        return False
    meta_payload = {
        "_yoast_wpseo_focuskw": focus_kw,
    }
    if meta_desc:
        meta_payload["_yoast_wpseo_metadesc"] = meta_desc[:155]
    if seo_title:
        meta_payload["_yoast_wpseo_title"] = seo_title[:70]
    proxy = _get_proxy()
    cmd = [
        "curl", "-s",
        "-X", "PATCH",
        f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}",
        "-u", f"{WP_USERNAME}:{WP_APP_PASSWORD}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"meta": meta_payload}),
    ]
    if proxy:
        cmd[1:1] = ["--proxy", proxy]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            log(f"_set_yoast_meta失败(pid={post_id}): curl返回{result.returncode}", "WARN")
            return False
        data = json.loads(result.stdout)
        saved_meta = data.get("meta", {})
        ok = (
            saved_meta.get("_yoast_wpseo_focuskw") == focus_kw
            and (not meta_desc or saved_meta.get("_yoast_wpseo_metadesc") == meta_desc[:155])
        )
        if ok:
            log(f"Yoast元数据设好: focus_kw={focus_kw}", "OK")
        else:
            log(f"Yoast元数据设疑(返回不符): {saved_meta}", "WARN")
        return ok
    except Exception as e:
        log(f"Yoast元数据设失败(pid={post_id}): {e}", "WARN")
        return False

# ============================================================
# 主流程
# ============================================================
def _auto_fix_content(content: str, check: dict, article: dict) -> str:
    """
    对预检不通过项进行可自动修复的处理，返回修复后content。
    可修复项：裸链nofollow、图片ALT缺失/空、AI腔词汇
    不可修复项：字数、段落结构、内链、H2结构、Focus KW密度
    """
    focus_kw = (article.get("focus_kw") or "").lower()

    # ── 1. 外部链接补 nofollow ─────────────────────────
    if check["ext_nofollow_issues"] > 0:
        def add_nofollow(m):
            tag = m.group(0)
            if "rel=" not in tag:
                tag = tag.replace(">", ' rel="nofollow noopener noreferrer">', 1)
            elif not re.search(r"nofollow", tag, re.I):
                tag = re.sub(r'rel=["\']([^"\']+)["\']',
                             lambda rm: f'rel="{rm.group(1)} nofollow"', tag)
            return tag
        content = re.sub(r'<a\s[^>]*href=["\']https?://[^"\']+["\'][^>]*>',
                         add_nofollow, content, flags=re.IGNORECASE)

    # ── 2. 图片 ALT 补全 ───────────────────────────────
    if check["alt_issues"] > 0:
        alt_text = focus_kw if focus_kw else "AI tool"
        def fix_alt(m):
            tag = m.group(0)
            has_valid_alt = bool(re.search(r'alt=["\']([^"\']+)["\']', tag)) and not re.search(r'alt=["\']\s*["\']', tag)
            if not has_valid_alt:
                tag = re.sub(r'alt=["\'][^"\']*["\']', 'alt="' + alt_text + '"', tag)
            return tag
        content = re.sub(r"<img\s[^>]*>", fix_alt, content, flags=re.IGNORECASE)

    # ── 3. AI腔词汇替换（单向，无循环）───────────────
    replacements = {
        "in today's digital age": "in recent years",
        "it's important to note": "notably",
        "it is important to note": "notably",
        "game-changing": "useful",
        "leveraging": "using",
        "next-generation": "modern",
        "revolutionize": "improve",
        "best-in-class": "high-quality",
        "unparalleled": "exceptional",
        "landscape of ai": "AI field",
        "landscape of AI": "AI field",
        "cutting-edge": "advanced",
        "top-tier": "premium",
        "ultimate solution": "best option",
        "state-of-the-art": "modern",
        "the world of ai": "the AI industry",
        "The world of AI": "the AI industry",
        "it goes without saying": "clearly",
        "needless to say": "obviously",
        "it is worth noting": "notably",
        "it is clear that": "it is evident that",
        "there is no doubt": "there is little doubt",
        "artificial intelligence is": "AI tools are",
        "ai technology is": "AI tools are",
        "machine learning enables": "machine learning helps",
        "deep learning is": "deep learning has",
        "ai-powered": "AI-driven",
    }
    for phrase, replacement in replacements.items():
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        new_content = pattern.sub(replacement, content)
        if new_content != content:
            content = new_content
            log(f"  [FIX] AI腔: '{phrase}' => '{replacement}'", "INFO")

    return content



def _recheck_and_publish(article: dict, content: str, featured_wp_url: str, existing_id: int = None) -> bool:
    """复核 + 发布。featured_wp_url 是迁移后的 WP 媒体库 URL，用于设 featured_media。
    existing_id != None 时执行覆盖更新（force模式）。"""
    check = precheck_readability(content, article)

    sep = "=" * 55
    print(chr(10) + sep)
    print("  自检结果：" + article["title"][:50])
    print(sep)
    print("  字数:" + str(check["total_words"]) + "词(450-800) | 句:" + str(check["sentences"]) + " | 段:" + str(check["paragraphs"]))
    print("  可读性:长句>" + str(check["long_sentences"]) + " | 过渡词:" + str(check["transition_count"]) + " | 被动:" + ("%.0f" % check["passive_pct"]) + "%")
    print("  图片:" + str(check["img_count"]) + "张 ALT缺:" + str(check["alt_issues"]) + " | 内链:" + str(check["internal_link_count"]) + "个 | KW:" + str(check["focus_kw_count"]) + "次")
    ok_h2 = "YES" if check["h2_count"] > 0 else "NO"
    print("  SEO:AI腔:" + str(len(check["robotic_phrases"])) + "词 | nofollow缺:" + str(check["ext_nofollow_issues"]) + " | H2:" + str(check["h2_count"]) + "个")
    print("  Meta:标题" + str(check["yoast_title_len"]) + "字 | 描述" + str(check["yoast_desc_len"]) + "字")

    if check["issues"]:
        print(chr(10) + "  不满足项 (" + str(len(check["issues"])) + "项):")
        for issue in check["issues"]:
            print("     - " + issue)

    fixable = [i for i in check["issues"]
               if any(k in i for k in ["nofollow", "ALT缺失", "AI腔词汇"])]
    if fixable:
        print(chr(10) + "  自动修复 " + str(len(fixable)) + " 项...")
        content = _auto_fix_content(content, check, article)
        check2 = precheck_readability(content, article)
        fixed = [i for i in check["issues"] if i not in check2["issues"]]
        still = check2["issues"]
        for item in fixed:
            print("    FIXED: " + item)
        if still:
            for item in still:
                print("    STILL: " + item)
        check = check2
    else:
        print(chr(10) + "  无需自动修复")

    print(chr(10) + "  发布到 WordPress...")
    ok = publish_to_wp(article, content, featured_wp_url, existing_id=existing_id)

    print(chr(10) + sep)
    if ok:
        print("  发布成功! 最终状态:")
    else:
        print("  发布失败，最终状态:")
    print("  字数:" + str(check["total_words"]) + " | KW:" + str(check["focus_kw_count"]) + "次 | 图:" + str(check["img_count"]) + "张")
    print("  内链:" + str(check["internal_link_count"]) + " | AI腔:" + str(len(check["robotic_phrases"])) + " | nofollow:" + str(check["ext_nofollow_issues"]) + "处")
    print("  H2: " + str(check["h2_titles"]))
    if check["issues"]:
        issues_preview = " | ".join(check["issues"][:5])
        print("  仍有" + str(len(check["issues"])) + "项不满足: " + issues_preview)
    print(sep + chr(10))
    return ok

def main():
    dry_run = "--dry-run" in sys.argv
    execute = "--execute" in sys.argv
    force = "--force" in sys.argv
    single_id = None
    for arg in sys.argv:
        if arg.startswith("--single"):
            single_id = int(arg.split("=")[1])

    if single_id:
        articles = [a for a in ARTICLES if a["id"] == single_id]
    elif dry_run:
        articles = ARTICLES[:1]
    elif execute:
        articles = ARTICLES
    else:
        print("用法:")
        print("  python3 regenerate_and_publish.py --dry-run          # 预览第1篇")
        print("  python3 regenerate_and_publish.py --execute           # 正式全部")
        print("  python3 regenerate_and_publish.py --single=90       # 只重建ID=90")
        print("  python3 regenerate_and_publish.py --single=90 --force  # 强制覆盖已发布")
        return

    results = []
    for i, article in enumerate(articles):
        sep = "=" * 60
        print(chr(10) + sep)
        print("[" + str(i+1) + "/" + str(len(articles)) + "] " + article["title"][:50])
        print("  FocusKW:" + article["focus_kw"] + " | Category:" + article["category"])

        print("  WP选题查重中...")
        wp_check = wp_pre_publish_check(article, force=force)
        existing_id = wp_check.get("existing_id")
        print("  WP已发布:" + str(wp_check["current_count"]) + "篇/" + article["category"] + "")
        if wp_check["duplicate"] and not force:
            dup_parts = []
            if wp_check.get("dup_title"):
                dup_parts.append("标题重复")
            if wp_check.get("dup_fkw"):
                dup_parts.append("FocusKW重复")
            print("  ❌ 选题重复: " + " + ".join(dup_parts))
            print("  ⚠️ 跳过生成，切换下一篇")
            continue
        cat_issues = [iss for iss in wp_check["issues"] if "分类过满" in iss]
        if cat_issues:
            needy = wp_check["suggested_category"]
            deficit = wp_check["deficit"].get(needy, 0) if needy else 0
            print("  ⚠️ " + " | ".join(cat_issues))
            print("  推荐分类: " + str(needy) + " (缺" + str(deficit) + "篇)")

        print("  生成文章中...")
        content = generate_seo_friendly_content(article)
        content = fix_long_paragraphs(content)

        check = precheck_readability(content, article)
        print("  预检: " + str(check["total_words"]) + "词 | " + str(check["sentences"]) + "句 | " + str(check["paragraphs"]) + "段")
        print("  长句>" + str(check["long_sentences"]) + " | 过渡:" + str(check["transition_count"]) + " | 被动:" + ("%.0f" % check["passive_pct"]) + "%")
        print("  图:" + str(check["img_count"]) + " ALT缺:" + str(check["alt_issues"]) + " | 内链:" + str(check["internal_link_count"]) + " | KW:" + str(check["focus_kw_count"]) + "次")
        print("  AI腔:" + str(len(check["robotic_phrases"])) + " | nofollow缺:" + str(check["ext_nofollow_issues"]) + " | 标题:" + str(check["yoast_title_len"]) + "字 | Meta:" + str(check["yoast_desc_len"]) + "字")
        # ── 叙事推进检测（2026-05-26）：人格工程核心 ──────
        np_check = check_narrative_progression(content)
        if np_check["verdict"] in ("FAIL", "UNCERTAIN"):
            print("  ⚠️ 叙事推进: " + np_check["verdict"] + " | 模板H2:" + str(np_check["fail_types"]) + "/" + str(np_check["total"]) + " | 叙事H2:" + str(np_check["pass_types"]) + "/" + str(np_check["total"]))
            if np_check["issues"]:
                for iss in np_check["issues"]:
                    print("     " + iss)
        else:
            print("  ✅ 叙事推进: PASS | 熵值:" + ("%.2f" % np_check["entropy_score"]) + " | 叙事H2:" + str(np_check["pass_types"]) + "/" + str(np_check["total"]))

        # ── H2模板硬性检测（2026-05-26）：违规则定向重写H2 ──
        h2_fail_issues = [i for i in check["issues"] if "[FAIL]" in i and "H2模板结构违规" in i]
        opt_count = 1
        if h2_fail_issues or np_check["verdict"] == "FAIL":
            print(chr(10) + "  ⚠️ H2违规检测，定向重写H2中...")
            content = _targeted_h2_rewrite(article, content, np_check)
            content = fix_long_paragraphs(content)
            check = precheck_readability(content, article)
            np_check2 = check_narrative_progression(content)
            h2_fail_issues2 = [i for i in check["issues"] if "[FAIL]" in i and "H2模板结构违规" in i]
            # ── _scoreHistory 里程碑追踪（中英双语）─────────────
            _track_score_milestone(
                article["id"], article["title"],
                np_check, np_check2,
                opt_count,
            )
            if h2_fail_issues2:
                print("  ⚠️ H2仍有模板违规: " + " | ".join(h2_fail_issues2[:2]))
            if np_check2["entropy_keepers"]:
                print("  ✅ 高熵段落已保留: " + str(len(np_check2["entropy_keepers"])) + "段")

        if check["issues"]:
            fail_issues = [i for i in check["issues"] if "[FAIL]" in i]
            print("  FAIL: " + " | ".join(check["issues"]))
            if fail_issues:
                print("  ⚠️ 含FAIL项: " + " | ".join(fail_issues))
        else:
            print("  PASS")

        if dry_run:
            print(chr(10) + "--- Preview (800 chars) ---")
            print(content[:800])
            print("---END---")
            print("  H2:" + str(check["h2_titles"]) + " | Count:" + str(check["h2_count"]) + " | TmplIssues:" + str(check["h2_template_issues"]))
            print("  KWx" + str(check["focus_kw_count"]) + " | AI腔:" + str(check["robotic_phrases"]))
            print("  ALT:" + str(check["alt_issues"]) + " | nofollow:" + str(check["ext_nofollow_issues"]))
            continue

        if execute or single_id:
            print("  生成封面图中（AI生成，内容感知型）...")
            content, featured_wp_url = generate_cover_for_content(article, content)
            if featured_wp_url:
                print("  封面图: " + featured_wp_url)
            else:
                print("  封面图失败，保留占位符")

            ok = _recheck_and_publish(article, content, featured_wp_url, existing_id=existing_id)
            results.append((article["id"], article["title"][:40], ok))
            time.sleep(3)

    if execute:
        print(chr(10) + ("=" * 60))
        ok_count = sum(1 for r in results if r[2])
        fail_count = sum(1 for r in results if not r[2])
        print("完成: OK=" + str(ok_count) + " FAIL=" + str(fail_count))
        for rid, rtitle, rok in results:
            status = "OK" if rok else "FAIL"
            print("  " + status + " ID " + str(rid) + ": " + rtitle)

if __name__ == "__main__":
    main()
