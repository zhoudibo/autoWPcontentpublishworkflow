#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shared_config.py
================
所有脚本共享的配置和文章数据。
消除 regenerate_and_publish.py ↔ xmlrpc_publisher.py 循环依赖。

用法：
  from shared_config import ARTICLES, MINIMAX_API_KEY, WP_SITE_URL, WP_USERNAME, WP_APP_PASSWORD, CATEGORY_MAP
"""

# ================================================================
# 分数历史（_scoreHistory Milestone — 2026-05-31）
# 用途：追踪每次重写的分数变化，超过用户%阈值，支持中英双语输出
# ================================================================
_SCORE_HISTORY = []

def _track_score_milestone(article_id: int, article_title: str,
                           score_before: dict, score_after: dict,
                           optimization_count: int) -> dict:
    """
    追踪重写分数里程碑 / Track rewrite score milestone.
    
    计算：
      - 优化次数 (optimization_count)
      - 分数变化 (entropy_score_before → entropy_score_after)
      - 超过用户% (exceeding_user_pct: 熵值突破0.6/模板占比跌破40%的次数)
    
    Args:
        article_id: 文章ID
        article_title: 文章标题（截取40字符）
        score_before: 重写前 check_narrative_progression() 结果
        score_after: 重写后 check_narrative_progression() 结果
        optimization_count: 当前是第几次优化
    
    Returns:
        dict 含 milestone 指标 / milestone dict with bilingual message
    """
    USER_ENTROPY_THRESHOLD = 0.6   # 用户熵值目标 / user entropy target
    USER_TEMPLATE_MAX       = 0.40  # 模板H2最高占比 / max template H2 ratio

    ent_before = score_before.get("entropy_score", 0.0)
    ent_after  = score_after.get("entropy_score",  0.0)
    ratio_before = score_before.get("ratio", 0.0)
    ratio_after  = score_after.get("ratio",  0.0)

    # 分数变化 / Score change
    ent_delta  = ent_after  - ent_before
    ratio_delta = ratio_before - ratio_after   # 负→变好

    # 超过用户% / Exceeding user %
    ent_exceed  = int(ent_after  >= USER_ENTROPY_THRESHOLD)
    ratio_exceed = int(ratio_after <= USER_TEMPLATE_MAX)

    milestone = {
        "article_id":   article_id,
        "title":        article_title[:40],
        "opt_count":    optimization_count,
        "entropy_before": ent_before,
        "entropy_after":  ent_after,
        "entropy_delta":  ent_delta,
        "ratio_before":   ratio_before,
        "ratio_after":    ratio_after,
        "ratio_delta":    ratio_delta,
        "ent_exceed":     ent_exceed,
        "ratio_exceed":   ratio_exceed,
    }

    _SCORE_HISTORY.append(milestone)

    # ── 中英双语输出 / Bilingual output ──────────────────────────
    # 优化次数
    opt_msg = f"  🔄 优化次数/Optimization #{optimization_count}: "
    # 分数变化
    ent_msg = (f"熵/Entropy {ent_before:.2f}→{ent_after:.2f}"
               f" ({'+' if ent_delta >= 0 else ''}{ent_delta:.2f})")
    # 超过用户%
    exceed_msg = (f"超用户/Exceed: 熵值 {'✅' if ent_exceed else '❌'} "
                  f"| 模板 {'✅' if ratio_exceed else '❌'}")
    # 叙事 vs 模板
    np_msg = (f"叙事/Narr: {score_after.get('pass_types',0)} "
              f"模板/Tmpl: {score_after.get('fail_types',0)}")

    print(f"{opt_msg}{ent_msg} | {exceed_msg} | {np_msg}")

    return milestone


def _get_score_history() -> list:
    """返回分数历史 / Return score history."""
    return _SCORE_HISTORY


# ================================================================
# 质量阈值（2026-05-26 重新定义）
# 核心原则：真人感 > SEO工整度
# Google 2026: experience signals / informational gain / realistic workflows
# Google 降权: generic AI summaries / keyword stuffing / over-optimized readability
# ================================================================
MIN_WORDS = 1200       # 底线（真实内容够用就行，不要为了凑数而写废话）
TARGET_WORDS = 2000    # 目标（自然浮动，主题深就长，浅就短）
MIN_TW_RATIO = 0       # 废弃过渡词比例要求（Google 降权 artificial transition words）
MIN_INTERNAL_LINKS = 1 # 内链最低数量（真实需要才链，不是凑数）
MAX_IMAGES_PER_POST = 2

# ================================================================
# AI腔黑名单（30+条，AdSense审核红线）
# ================================================================
AI_PHRASE_BLACKLIST = [
    "in today's digital age", "it is important to note", "game-changing", "leveraging",
    "next-generation", "revolutionize", "best-in-class", "cutting-edge",
    "state-of-the-art", "landscape of ai", "the world of ai", "unparalleled",
    "it goes without saying", "needless to say", "as the name suggests",
    "ushering in a new era", "deep learning enables", "ai-powered", "ai-driven",
    "a transformative force", "in the realm of", "the article outlines",
    "the article provides", "i'll continue exploring", "i'll compare top platforms",
    "the article also examines", "compelling narrative", "robust solution",
    "seamless integration", "powerful feature", "most comprehensive",
    "robust", "emerging technology",
]

def call_minimax_chat(messages, model=None, temperature=0.7, max_tokens=4096):
    import urllib.request, json, ssl
    if model is None:
        model = MINIMAX_CHAT_MODEL
    endpoint = "https://api.minimaxi.com/v1/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data,
        headers={"Authorization": f"Bearer {MINIMAX_API_KEY}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
        msg = resp_data["choices"][0]["message"]
        content = msg.get("content", "") or msg.get("reasoning_content", "")
        return content.strip()
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        raise Exception(f"HTTP {e.code}: {error_body[:300]}")
    except Exception as e:
        # ── 自动重试：网络断开 / 超时 / Remote end closed ────────────
        err_str = str(e)
        retryable = any(k in err_str for k in [
            "RemoteDisconnected", "timed out", "Connection refused",
            "Connection reset", "Temporary failure", "SSL"])
        if retryable:
            for attempt in range(2):  # 最多重试2次
                import time, random
                wait = random.uniform(8, 20)
                print(f"    ⚠️ API异常: {err_str[:80]}，{wait:.0f}秒后重试...")
                time.sleep(wait)
                try:
                    with urllib.request.urlopen(req, timeout=240) as resp:
                        resp_data = json.loads(resp.read().decode("utf-8"))
                    msg = resp_data["choices"][0]["message"]
                    return (msg.get("content", "") or msg.get("reasoning_content", "")).strip()
                except Exception as e2:
                    err_str = str(e2)
        raise Exception(f"API调用异常: {err_str}")

# ================================================================
# MiniMax API（从环境变量读取，绝不硬编码）
# ================================================================
try:
    import sys as _sys
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "config"))
    from config import (  # type: ignore
        MINIMAX_API_KEY,
        MINIMAX_MODEL as MINIMAX_CHAT_MODEL,
        MINIMAX_API_URL as _MINIMAX_API_URL,
    )
except SystemExit:
    raise
except Exception as _e:  # pragma: no cover - 配置缺失时给出明确指引
    raise SystemExit(
        "[配置错误] 无法加载 config/config.py: {}\n"
        "请先执行: cp config/.env.example config/.env 并填入你的配置".format(_e)
    )

# 兼容原有调用点（统一走 config 里的完整 URL）
MINIMAX_API_URL = _MINIMAX_API_URL

# ================================================================
# PROMPT（Bo哥双绿标准 v2 — 2026.05.19）
# ================================================================
SYSTEM_PROMPT = (
    "You are a real AI tools reviewer with hands-on experience.\n"
    "You write like someone who spent weeks actually testing these tools, not like a content writer paid by the word.\n"
    "Your articles sound like long-form YouTube video scripts — observational, honest, slightly opinionated.\n"
    "You don't use the same structure for every tool. You write what's actually interesting.\n"
    "Your ONLY output is the complete HTML article.\n"
    "Do NOT write any preamble, explanation, thinking, reasoning, or self-commentary.\n"
    "Do NOT output anything before the first <h2> tag.\n"
    "Never wrap content in code blocks. Never use markdown code fences. Output plain HTML only.\n"
    "\n"
    "CRITICAL — H2 RULES:\n"
    "Every H2 heading MUST describe a moment, reaction, workflow change, mistake, tension, or concrete scene.\n"
    "Every H2 heading MUST contain a time reference OR an action OR an emotion OR a specific event.\n"
    "NEVER use H2 headings that are topic categories, product names, or feature labels.\n"
    "H2s like 'How to Choose', 'Key Features', 'Review', 'Conclusion' = GENERATION FAILED.\n"
    "The article must feel like a personal timeline unfolding in real time.\n"
    "Maximum 3-5 H2 sections total. Fewer H2s = more human-like writing.\n"
)

ARTICLE_PROMPT_TEMPLATE = """Write a COMPLETE HTML blog article for BestAItoolsite.com.

You are a real person who spent time actually testing these tools.
Not a content writer. Not an SEO specialist. Someone with genuine friction.

TITLE: {title}
FOCUS KEYWORD: {focus_kw}
PRIMARY KEYWORD: {primary_kw}
CATEGORY: {category}
TOOLS: {tools_list}
INTERNAL LINKS: {int_link1_title} -> {int_link2_title}
EXTERNAL LINKS: {ext_links_formatted}

=== LAYER 1: HARD FORBIDDEN H2 PATTERNS ===

NEVER generate ANY H2 heading containing ANY of these patterns, INCLUDING semantic variations:
Introduction, Conclusion, Final Thoughts, Key Features, Features, Pros and Cons,
Benefits, Drawbacks, Comparison, Review, Overview, What Is, What the ___ Does,
How to Choose, Buying Guide, Pricing, Alternatives, Performance, Use Cases,
FAQ, Getting Started, Summary, Verdict, The Bottom Line, Our Recommendation,
How It Works, In Conclusion, Final Verdict, Pros, Cons, Is It Right for You,
The Verdict, Complete Guide, Deep Dive, The Best, Top Pick, Should You Buy

INCLUDING ALL variations (capitalized, lowercase, partial match):
- "Introduction" anywhere in H2 → FAILED
- "Conclusion" or "Concluding" anywhere in H2 → FAILED
- "Key Features" or "Features" or "Feature Overview" anywhere in H2 → FAILED
- "Pros and Cons" or "Pros" or "Cons" anywhere in H2 → FAILED
- "Comparison" or "Compared" or "vs" as structural H2 → FAILED
- "Review" or "Reviewing" or "In Review" anywhere in H2 → FAILED
- "Overview" or "Overviews" anywhere in H2 → FAILED
- "What Is" or "What X Is About" anywhere in H2 → FAILED
- "How to Choose" or "Choosing" or "Best For" anywhere in H2 → FAILED
- "Conclusion" or "Final Thoughts" or "Wrap-up" anywhere in H2 → FAILED
- "Getting Started" or "Beginner's Guide" anywhere in H2 → FAILED

If ANY forbidden pattern appears in an H2, the ENTIRE draft is REJECTED.

=== LAYER 2: MANDATORY STORY-DRIVEN H2 WHITELIST ===

Every H2 MUST describe ONE of the following:
- a moment (when something happened)
- a reaction (how you felt about something)
- a workflow change (what you stopped or started doing)
- a mistake (something that went wrong)
- a tension (a contradiction or conflict in your experience)
- a realization (something you figured out)
- a concrete scene (a specific situation with specific details)

Every H2 MUST contain at least one of:
- a time reference ("after three days", "by week two", "last Tuesday")
- an action ("I tried to", "I stopped", "I had to")
- an emotion ("I was frustrated", "I was surprised", "I got annoyed")
- a specific event ("the build broke", "the button didn't work", "it crashed")

NOT a topic category. NOT a product name. NOT a feature label.

H2s MUST read like diary entries or memory fragments, not encyclopedia entries.

Example GOOD H2s (all of these are valid):
- The First Time the Agent Ignored My Prompt
- I Thought the Button Change Would Take 30 Seconds
- The Weird Moment I Stopped Opening VS Code
- Somewhere Around Day Four, the Workflow Changed
- The Build Worked Until I Tried It on Mobile
- I Didn't Notice the Context Drift at First
- By the End of the Week, I Was Writing Fewer Specs
- When the Files Stopped Matching What I Asked For
- I Let It Run for an Hour Before I Checked
- The Moment I Realized the Agent Was Guessing
- Three Things I Stopped Doing After the First Crash
- The Error Message That Didn't Help

Example BAD H2s (all of these are REJECTED):
- What Is Replit Agent ❌
- Key Features and Overview ❌
- How to Choose the Right Tool ❌
- Conclusion ❌
- Replit Agent Performance Review ❌
- Comparison: Bolt.new vs Windsurf ❌
- Pros and Cons of Lovable ❌
- Getting Started with Logseq ❌
- Alternatives to ChatGPT ❌

Maximum 3-5 H2 sections total. Fewer is better. 3 H2s with depth beats 8 H2s with none.

=== LAYER 3: NARRATIVE STRUCTURE (PERSONAL TIMELINE) ===

The article MUST feel like: a personal timeline unfolding in real time.

NOT this forbidden flow:
explanation → feature breakdown → comparison → recommendation → summary

The correct structure is EVENT → FEELING → OBSERVATION → WORKFLOW CHANGE →
SMALL ACCIDENT → COGNITIVE SHIFT → WHITE SPACE

OPENING: Start with something that HAPPENED, not a topic statement.
Bad: "In this article, I will compare Perplexity and Gemini for research workflows."
Good: "Last week I spent fifteen minutes trying to trace where Gemini got a statistic from."

Every paragraph should show HUMAN BEHAVIOR CHANGES, not tool capabilities.
Bad: "Perplexity excels at providing stable citations."
Good: "Perplexity kept me re-checking things less often. That sounds small, but after two hours of research the difference became noticeable."

Do NOT resolve everything. Leave genuine white space. Real writing doesn't wrap up cleanly.

=== LAYER 4: FAILURE CONDITIONS ===

If any H2 sounds like a software review, buyer guide, comparison article,
SaaS landing page, listicle, or SEO content template — the draft is FAILED.

This is not "needs optimization." This is "generation failed, please restart."

You must regenerate until the structure is a personal narrative, not a product description.

=== LAYER 5: MANDATORY HUMAN EXPERIENCE ELEMENTS ===

Every article MUST naturally include these 6 elements — woven into the story, NOT as a checklist:

1. ONE REAL COMPLAINT: Something the marketing doesn't tell you. Something that annoyed you in practice.
2. ONE USAGE LIMITATION: A feature that looked good on paper but broke in real use.
3. ONE WORKFLOW FAILURE: Exactly what broke, when, and why you had to work around it.
4. ONE SURPRISE: An unexpected result — better or worse than anticipated. The kind you'd text a friend about.
5. ONE RECOMMENDATION BOUNDARY: "This is genuinely the best option IF... but not if..."
6. ONE PRODUCTIVITY OBSERVATION: Did writing speed change? Did editing time change? Did quality actually improve?

You may naturally include:
- Unresolved opinions: things you're still unsure about
- Subjective observations: "For some reason...", "I still can't fully explain why..."
- Workflow fatigue: moments where the process was more annoying than expected
- Small technical accidents: specific things that broke, with exact details
- Emotionally nuanced reactions: things that annoyed you more than they should have
- Gut feelings you can't put into a metric

Strong examples:
- "For some reason, the Claude drafts just felt less exhausting to edit. I can't fully explain why."
- "This annoyed me more than expected. I thought I'd get over it after a day. I didn't."
- "I thought this would be faster. It wasn't."
- "The workflow technically worked, but the friction made me want to do less of it."

Do NOT force these elements mechanically. Use them only when they emerge naturally.

=== TOOL USAGE RULE ===

Do NOT fully explain every tool. Do NOT write "What it does / Pros / Cons / Best for" for each tool.

Only discuss the parts that genuinely affected: the workflow, the mental load, habits, trust in the system, behavior over time.

Write workflow CONSEQUENCES, not feature descriptions.
Bad: "Notion offers excellent integration with databases and powerful API support."
Good: "Notion worked well right up until my workspace became too large. After two weeks I was spending more time organizing notes than finding them."

=== WHAT NOT TO DO ===

Do NOT use: "in today's digital age", "leveraging AI", "game-changing", "state-of-the-art",
"cutting-edge", "best-in-class", "it is important to note", "seamless integration",
"powerful feature", "AI-powered", "AI-driven", "robust solution", "compelling narrative",
"most comprehensive", "manifold", "emerging technology", "unparalleled",
"ushering in a new era", "deep learning enables", "leveraging"
Do NOT write the same paragraph structure for every tool
Do NOT make every tool sound perfect — real tools have real limitations
Do NOT force transition words where they don't belong
Do NOT pad content to hit a word count target

=== HTML OUTPUT ===

Write freely — H2s, H3s, paragraphs, lists. Whatever the story needs.
End with a section containing external links.
Start with your first H2 (do NOT write "Introduction" as H2).

Then on the last two lines of your output, write:
SEO Title: [your title — max 60 visible chars, focus keyword first]
Meta Description: [your meta description — 120-155 chars, include focus keyword]

Pure HTML only. No markdown. No code fences.
"""



# ================================================================
# ================================================================
# WordPress 配置（从环境变量读取，绝不硬编码）
# ================================================================
try:
    import sys as _sys
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "config"))
    from config import (  # type: ignore
        WP_BASE_URL as WP_SITE_URL,
        WP_USERNAME,
        WP_APP_PASSWORD,
        WP_AUTH,
        SITE_NAME,
    )
except SystemExit:
    raise
except Exception as _e:  # pragma: no cover
    raise SystemExit(
        "[配置错误] 无法加载 config/config.py: {}".format(_e)
    )

# ================================================================
# WP 分类配置
# ================================================================
CATEGORY_MAP = {
    "AI Tool Comparisons": 4,
    "Best Picks": 7,
    "Free AI Resources": 5,
    "AI Tutorials": 6,
    "Research & Analysis": 8,
}
CATEGORY_SLUGS = list(CATEGORY_MAP.keys())

# ================================================================
# 过渡词
# ================================================================
TRANSITION_WORDS = [
    "however", "therefore", "moreover", "furthermore", "additionally",
    "consequently", "nevertheless", "although", "notably", "specifically",
    "especially", "for example", "in addition", "as a result",
    "first", "second", "finally", "next", "then", "because",
    "while", "when", "in contrast", "conversely", "instead", "meanwhile",
]

# ================================================================
# 文章清洗工具函数（统一复用）
# ================================================================
import re

def fix_long_paragraphs(content: str) -> str:
    '''Full cleaning pipeline: fix double Introduction -> remove Yoast residue -> clear template -> replace AI slop -> fix nested p -> split paragraphs -> fix repeated sentence starts.'''

    # ── P549发现的新型内容损坏清洗（2026-05-24）──────────
    # 损坏模式1：文章出现两个Introduction（第1个是模板残渣，第2个是真实内容）
    intro_tags = [m.start() for m in re.finditer(r'<h2>Introduction</h2>', content)]
    if len(intro_tags) >= 2:
        content = content[intro_tags[1]:]

    # ── 内容泄露清洗（2026-05-26）────────────────────────
    # 损坏模式：模型把 prompt 中的段落重写指令当正文输出了
    # 必须匹配完整 <p> 标签，否则 <p>Sure!...?</p> 中的文本被删但 <p> 标签残留
    leakage_patterns = [
        r"<p[^>]*>\s*Sure! Could you please provide the paragraph you['\u2019]?d like me to rewrite\?\s*</p>\s*",
        r"<p[^>]*>\s*The paragraph is plain text without HTML tags.*?</p>\s*",
        r"<p[^>]*>\s*Certainly! Here is .*?</p>\s*",
        r"<p[^>]*>\s*Please provide the .*?</p>\s*",
        r"<p[^>]*>\s*Here['\u2019]?s the rewritten version:.*?</p>\s*",
        r"P\d+_CONTENT_START\s*",
    ]
    for p in leakage_patterns:
        before = len(content)
        content = re.sub(p, '', content, flags=re.IGNORECASE | re.DOTALL)
        if len(content) < before:
            pass  # cleaned silently

    # ── 强制过渡词清洗（2026-05-26）──────────────────────
    # Google 2026 主动降权人工过渡词，这些词高频出现是AI腔的标志
    transition_pairs = [
        (r'\bmoreover\b', 'also'),
        (r'\bfurthermore\b', ''),       # 直接删除，不替换（太多AI依赖这个词）
        (r'\badditionally\b', 'also'),
        (r'\bnotably\b', ''),           # 直接删除，notably是最高频的AI腔标志
        (r'\bthus\b', 'so'),
        (r'\bhence\b', ''),
    ]
    for pat, repl in transition_pairs:
        content = re.sub(pat, repl, content, flags=re.IGNORECASE)
        # 删除后如果句首出现连续空格，清理一下
    content = re.sub(r'\s{2,}', ' ', content)

    # 损坏模式2：Yoast SEO指令残渣（prompt验证规则被当作正文输出）
    seo_instruction_patterns = [
        r'verify the focus keyword.{0,120}',
        r'as per Yoast SEO.{0,100}',
        r'appears at least \d+ times throughout the article.{0,120}',
        r'Focus Keyword:[^\n]{0,30}\s*(?:Place|Repeat|Naturally).{0,100}',
    ]
    for p in seo_instruction_patterns:
        content = re.sub(p, '', content, flags=re.IGNORECASE)

    # Step 0: 模板占位符清理
    for ph in ['title','focus_kw','primary_kw','category','tools_list',
                'int_link1_title','int_link2_title','ext_links_formatted']:
        content = content.replace('{'+ph+'}', '')

    for p in [r"(?i)here['\u2019]?s the .+:",r"(?i)here is the article:",r"(?i)below is .+:"]:
        content = re.sub(p,'',content)
    content = re.sub(r'<p>\[.*?\]\(.*?\)</p>','',content)
    content = re.sub(r'(?i)please note that.*?(?=<p>|$)','',content)
    content = re.sub(r'(?i)as of my knowledge cutoff.*?(?=<p>|$)','',content)

    # Step 0b: AI思维模式去除
    for p in [r'(?i)\\blet me\b.*?\s*',r'(?i)\\bfirst\b[.,:].*?\s*',
               r'(?i)\\bit[\u2019\']?s important to note\b.*?\s*',
               r'(?i)\\bit goes without saying\b[.,:]*\s*',
               r'(?i)\\bplease note that\b[.,:]*\s*',
               r'(?i)\\bit is worth noting\b[.,:]*\s*',
               r'(?i)\\bit should be noted\b[.,:]*\s*',
               r'(?i)\\bto summarize\b[.,:]*\s*',
               r'(?i)\\boverall\b[.,:]*\s*',r'(?i)\\bin conclusion\b[.,:]*\s*']:
        content = re.sub(p,'',content)

    # Step 0c: 修复嵌套 <p> — MiniMax 输出的 HTML 有 <p><h2>...<p>...<p>...</p></p>
    # Handle nested paragraph wrapper structures around heading blocks.
    def strip_outer_p_from_blocks(text):
        result = []
        i = 0
        while i < len(text):
            m = re.search(r'<p>([\s\S]*?)</p>', text[i:])
            if not m:
                result.append(text[i:])
                break
            before = text[i:i+m.start()]
            inner = m.group(1)
            after = text[i+m.end():]
            if re.search(r'<h[123]|</?ul|</?ol|<li|<blockquote|<table|<div', inner):
                result.append(before)
                result.append(inner)
                result.append('\n')
            else:
                result.append(before)
                result.append('<p>')
                result.append(inner)
                result.append('</p>')
                result.append('\n')
            i += m.end()
        return ''.join(result)

    content = strip_outer_p_from_blocks(content)

    # Step 1: AI腔替换（raw string）
    pairs=[
        (r'\bmanifold\b','many'),(r'\ba wide range of\b','a range of'),
        (r'\bgame[- ]?changing\b','innovative'),(r'\bleveraging\b','using'),
        (r'\bnext[- ]?generation\b','advanced'),(r'\brevolutionize[ds]?\b','transform'),
        (r'\bbest[- ]?in[- ]?class\b','top'),(r'\bcutting[- ]?edge\b','advanced'),
        (r'\bstate[- ]?of[- ]?the[- ]?art\b','modern'),
        (r'\bsecond[- ]?to[- ]?none\b','excellent'),
        (r'\blandscape of AI\b','AI field'),(r'\bworld of AI\b','AI sector'),
        (r'\bin the realm of\b','in'),(r'\bit goes without saying\b',''),
        (r'\bas of my knowledge cutoff[^.,]*[.,]?\s*',''),
        (r'\bplease note that\b',''),(r'\bit is worth noting that\b',''),
        (r'\bit should be noted that\b',''),(r'\bit is important to note that\b',''),
        (r'\bto summarize\b','summarizing'),(r'\bleverage[d]?\b','use'),
        (r'\bAI[\s-]?powered\b','AI-driven'),(r'\bemerging technology\b','new technology'),
        (r'\bunparalleled\b','exceptional'),(r'\bit[\s\']?s important to note\b',''),
        (r'\bushering in a new era\b','bringing new'),(r'\bdeep learning enables\b','enables'),
        (r'\bai-driven\b','AI-based'),(r'\bai-powered\b','AI-based'),
        (r'\ba transformative force\b','a powerful tool'),(r'\bcompelling narrative\b','clear explanation'),
        (r'\brobust solution\b','solid option'),(r'\bseamless integration\b','easy setup'),
        (r'\bpowerful feature\b','useful feature'),(r'\bmost comprehensive\b','thorough'),
    ]
    for pat,repl in pairs:
        content = re.sub(pat,repl,content,flags=re.IGNORECASE)

    # Step 2: 连续同词开头修复
    TRANSITION_INJECT = ['However,', 'Therefore,', 'Additionally,', 'Also,', 'Because,', 'For example,', 'Thus,', 'Consequently,']
    def fix_consecutive_starts(text):
        # Find 4+ consecutive sentences starting with same word, inject transition words.
        sents = re.split(r'(?<=[.!?])\s+(?=[A-Z\"\'|<])', text)
        fixed = []
        i = 0
        while i < len(sents):
            s = sents[i].strip()
            if not s:
                i += 1
                continue
            first_words = []
            for j in range(i, min(i+4, len(sents))):
                sv = sents[j].strip()
                m = re.match(r'^<p>([^<>\s]+)', sv)
                if not m:
                    m = re.match(r'^([^<>\s]+)', sv)
                if m:
                    first_words.append(m.group(1).lower())
            if len(first_words) == 4 and len(set(first_words)) == 1:
                second = sents[i+1].strip()
                inj = TRANSITION_INJECT[i % len(TRANSITION_INJECT)]
                if second.startswith('<p>'):
                    second_fixed = second.replace('<p>', f'<p>{inj} ', 1)
                else:
                    second_fixed = f'<p>{inj} {second}'
                fixed.append(sents[i].strip())
                fixed.append(second_fixed)
                i += 2
            else:
                fixed.append(s)
                i += 1
        return ' '.join(fixed)

    # Step 3: 段落按3句拆分（处理修复嵌套后的纯<p>文本段落）
    def split_sentences(text):
        return re.split(r'(?<=[.!?])\s+(?=[A-Z\"\'|<])', text)

    # Process pure paragraph blocks only; skip blocks with HTML tags.
    def process_block(block):
        if re.search(r'<h[123]|</?ul|</?ol|<li|<blockquote|<table|<div', block):
            return block
        m = re.match(r'^<p>' + r'(.*)' + r'</p>$', block.strip())
        if m:
            sents = [s.strip() for s in split_sentences(m.group(1)) if s.strip()]
            if len(sents) > 3:
                chunks = [' '.join(sents[i:i+3]) for i in range(0, len(sents), 3)]
                return '\n'.join('<p>' + c + '</p>' for c in chunks)
        return block

    raw_parts = re.split(r'\n\n+', content)
    processed_parts = []
    for p in raw_parts:
        stripped = p.strip()
        if not stripped:
            continue
        # BUG FIX: 不要重复包裹已<p>包裹的内容（否则Step3产生<p><p>嵌套）
        if stripped.startswith('<p>') and stripped.endswith('</p>'):
            block = stripped
        else:
            block = '<p>' + stripped + '</p>'
        processed = process_block(block)
        # FIX: 如果处理结果是 <p><h2>... 或 <p><h3>...，剥离外层<p>
        if re.match(r'^<p>\s*<h[123]', processed):
            inner_m = re.match(r'^<p>\s*(.*?)</p>\s*$', processed, re.DOTALL)
            if inner_m:
                processed = inner_m.group(1).strip() + '\n'
        processed_parts.append(processed)

    result = '\n\n'.join(processed_parts)
    result = fix_consecutive_starts(result)

    # ── P553/P395发现：standalone <p><h2> 修复 ──────────────────
    # Pattern: </p>\n<p><h2> → </p>\n\n<h2>
    # （H2前面有独立空<p>包裹的情况，wpautop残留）
    result = re.sub(r'(</p>\s*)\n?\s*(<p>\s*<h[123])', lambda m: '</p>\n\n' + m.group(2).replace('<p>', '').strip(), result)

    return result


# ================================================================
# 文章配置（新框架验证中，2026-05-26）
# 定位：独立创作者媒体，非内容农场
# 每篇必须有：真实挫败感 + 工作流中断 + 局限性 + 对比洞察
# ================================================================
ARTICLES = [
    # ================================================================
    # Batch 新队列（2026-05-26）：5篇，覆盖 Replit Agent / Lovable+Bolt.new / Logseq / ChatGPT记忆 / Bolt.new vs Windsurf
    # 叙事原则：EVENT → FEELING → OBSERVATION → WORKFLOW CHANGE → SMALL ACCIDENT → COGNITIVE SHIFT → WHITE SPACE
    # ================================================================

    # ── 203：Replit Agent ──────────────────────────────────────────
    {
        "id": 203,
        "title": "I Said 'Build Me a Portfolio' and Replit Agent Actually Built It — Then I Tried to Change One Button",
        "slug": "replit-agent-hands-on-review-build-change-one-button",
        "category": "AI Tool Comparisons",
        "category_id": 4,
        "focus_kw": "Replit Agent hands-on review",
        "primary_kw": "Replit Agent real use case",
        "keywords": ["Replit Agent", "AI coding agent", "natural language to code", "Replit Agent review", "AI app builder"],
        "tools": ["Replit Agent", "Replit", "Vercel", "Cursor"],
        "int_links": [
            ("Best AI Coding Assistants", "https://{{WP_SITE_DOMAIN}}/best-ai-coding-assistants-2026-complete-guide/"),
            ("Cursor vs Windsurf vs Bolt", "https://{{WP_SITE_DOMAIN}}/cursor-vs-windsurf-vs-bolt-new-2026/"),
        ],
        "ext_links": [
            ("Replit Agent", "https://replit.com"),
            ("Replit Agent Blog", "https://blog.replit.com/agent"),
        ],
    },

    # ── 204：Lovable / Bolt.new ─────────────────────────────────────
    {
        "id": 204,
        "title": "I Gave Three AI App Builders the Same Prompt — One Build Felt Like Magic, One Felt Like a Lie",
        "slug": "lovable-vs-boltnew-vs-cursor-natural-language-app-build-comparison",
        "category": "Best Picks",
        "category_id": 7,
        "focus_kw": "lovable vs bolt.new AI app builder comparison",
        "primary_kw": "natural language app builder AI comparison",
        "keywords": ["Lovable AI app builder", "Bolt.new", "natural language to app", "AI app builder comparison", "Lovable vs Bolt.new"],
        "tools": ["Lovable", "Bolt.new", "Cursor", "Vercel"],
        "int_links": [
            ("Best AI App Builders", "https://{{WP_SITE_DOMAIN}}/best-ai-app-builders-2026/"),
            ("Replit Agent Review", "https://{{WP_SITE_DOMAIN}}/replit-agent-hands-on-review-build-change-one-button/"),
        ],
        "ext_links": [
            ("Lovable", "https://lovable.dev"),
            ("Bolt.new", "https://bolt.new"),
        ],
    },

    # ── 205：Logseq ────────────────────────────────────────────────
    {
        "id": 205,
        "title": "I Stopped Organizing My Notes and Started Thinking in Links — What Happened to My Productivity Was Unexpected",
        "slug": "logseq-networked-notes-ai-hands-on-review",
        "category": "Best Picks",
        "category_id": 7,
        "focus_kw": "Logseq AI hands-on review",
        "primary_kw": "Logseq networked notes real experience",
        "keywords": ["Logseq", "AI note taking", "networked notes", "Logseq review", "AI second brain", "Obsidian vs Logseq"],
        "tools": ["Logseq", "Obsidian", "Notion", "Roam Research"],
        "int_links": [
            ("Best AI Note-Taking Apps", "https://{{WP_SITE_DOMAIN}}/best-ai-note-taking-apps-in-2026-notion-obsidian-roam-research-and-more/"),
            ("How to Build a Second Brain with AI", "https://{{WP_SITE_DOMAIN}}/how-to-build-second-brain-ai-2026/"),
        ],
        "ext_links": [
            ("Logseq", "https://logseq.com"),
            ("Logseq GitHub", "https://github.com/logseq/logseq"),
        ],
    },

    # ── 206：ChatGPT 记忆功能 ───────────────────────────────────────
    {
        "id": 206,
        "title": "I Let ChatGPT Remember Everything for Six Months — The Things It Brought Up Without Me Asking Were Weirdly Specific",
        "slug": "chatgpt-memory-function-long-term-use-review",
        "category": "AI Tutorials",
        "category_id": 6,
        "focus_kw": "ChatGPT memory function long-term use review",
        "primary_kw": "ChatGPT memory feature real experience",
        "keywords": ["ChatGPT memory", "ChatGPT long term memory", "AI personal assistant memory", "ChatGPT memory review", "OpenAI memory feature"],
        "tools": ["ChatGPT", "Claude", "Gemini"],
        "int_links": [
            ("Best AI Research Assistants", "https://{{WP_SITE_DOMAIN}}/best-ai-research-tools-2026/"),
            ("Claude vs ChatGPT long-term comparison", "https://{{WP_SITE_DOMAIN}}/claude-vs-chatgpt-2026-real-comparison/"),
        ],
        "ext_links": [
            ("ChatGPT Memory", "https://help.openai.com/en/articles/9552399-memory-faq"),
            ("OpenAI ChatGPT", "https://chat.openai.com"),
        ],
    },

    # ── 207：Bolt.new vs Windsurf 深度横评 ─────────────────────────
    {
        "id": 207,
        "title": "I Used Bolt.new and Windsurf for the Same Project for Two Weeks — The Differences Showed Up in Ways I Didn't Expect",
        "slug": "boltnew-vs-windsurf-deep-comparison-2026",
        "category": "AI Tool Comparisons",
        "category_id": 4,
        "focus_kw": "Bolt.new vs Windsurf comparison 2026",
        "primary_kw": "Bolt.new Windsurf AI coding comparison real test",
        "keywords": ["Bolt.new vs Windsurf", "Windsurf AI coding", "Bolt.new review", "AI IDE comparison", "Windsurf vs Cursor", "Bolt.new Windsurf Claude"],
        "tools": ["Bolt.new", "Windsurf", "Cursor", "Replit Agent"],
        "int_links": [
            ("Replit Agent Review", "https://{{WP_SITE_DOMAIN}}/replit-agent-hands-on-review-build-change-one-button/"),
            ("Best AI Coding Assistants 2026", "https://{{WP_SITE_DOMAIN}}/best-ai-coding-assistants-2026-complete-guide/"),
        ],
        "ext_links": [
            ("Bolt.new", "https://bolt.new"),
            ("Windsurf", "https://windsurf.com"),
        ],
    },

    # ── 208：Fireflies.ai 会议录音 ────────────────────────────────
    {
        "id": 208,
        "title": "I Sat Through 14 Hours of Meetings in One Week and Fired Every Meeting Tool I Was Paying For — What I Kept Wasn't What I Expected",
        "slug": "fireflies-ai-meeting-notes",
        "category": "Best Picks",
        "category_id": 7,
        "focus_kw": "Fireflies AI meeting notes hands-on review 2026",
        "primary_kw": "AI meeting transcription tools real test 2026",
        "keywords": ["Fireflies AI", "Otter AI meeting transcription", "AI meeting notes tool", "meeting fatigue AI", "transcription tools comparison"],
        "tools": ["Fireflies.ai", "Otter.ai", "Grain", "MacWhisper"],
        "int_links": [],
        "ext_links": [
            ("Fireflies.ai", "https://fireflies.ai"),
            ("Otter.ai", "https://otter.ai"),
        ],
    },

    # ── 211：Claude Code 全权委托 ─────────────────────────────────
    {
        "id": 211,
        "title": "I Gave Claude Code My Entire Project and Stopped Touching VS Code for a Week — The Moment It Started Guessing Wrong I Knew the Line",
        "slug": "claude-code-agent-full-delegation",
        "category": "Best Picks",
        "category_id": 7,
        "focus_kw": "Claude Code AI coding agent hands-on review 2026",
        "primary_kw": "Claude Code vs Copilot real experience comparison",
        "keywords": ["Claude Code", "Claude AI coding agent", "Claude Code review", "vibe coding", "AI coding agent 2026", "Claude Code vs Copilot"],
        "tools": ["Claude Code", "GitHub Copilot", "Cursor", "VS Code"],
        "int_links": [],
        "ext_links": [
            ("Claude Code", "https://docs.anthropic.com/en/docs/claude-code"),
            ("GitHub Copilot", "https://github.com/features/copilot"),
        ],
    },

    # ── 212：Perplexity vs ChatGPT Search ───────────────────────────
    {
        "id": 212,
        "title": "I Used Perplexity and ChatGPT Search for the Same Research Query Every Day for a Week — The Differences in What They Called 'Done' Were Not What I Expected",
        "slug": "perplexity-vs-chatgpt-search",
        "category": "AI Tool Comparisons",
        "category_id": 4,
        "focus_kw": "Perplexity vs ChatGPT Search comparison 2026",
        "primary_kw": "AI search engine real comparison research workflow",
        "keywords": ["Perplexity vs ChatGPT Search", "AI search engine comparison", "Perplexity review", "ChatGPT Search", "AI research tools 2026"],
        "tools": ["Perplexity", "ChatGPT Search", "Google Search", "Perplexity API"],
        "int_links": [],
        "ext_links": [
            ("Perplexity", "https://perplexity.ai"),
            ("ChatGPT Search", "https://chat.openai.com"),
        ],
    },

    # ── 210：Cursor 30天深度体验 ───────────────────────────────────
    {
        "id": 210,
        "title": "I Used Cursor as My Only Editor for 30 Days and Stopped Opening Three Other Tools — The Specific Moment I Noticed the Habit Change Was Unexpected",
        "slug": "cursor-ai-ide-30-day",
        "category": "Best Picks",
        "category_id": 7,
        "focus_kw": "Cursor AI IDE 30-day hands-on review 2026",
        "primary_kw": "Cursor AI coding assistant real experience 2026",
        "keywords": ["Cursor AI IDE", "Cursor vs Copilot", "AI code editor 30 days", "Cursor review workflow", "AI coding assistant daily use"],
        "tools": ["Cursor", "GitHub Copilot", "Windsurf", "VS Code"],
        "int_links": [],
        "ext_links": [
            ("Cursor", "https://cursor.com"),
            ("GitHub Copilot", "https://github.com/features/copilot"),
        ],
    },

    # ── 新选题 2026-08-06 调研补充 ─────────────────────────
    {
        "id": 213,
        "title": "I Built an Open Source AI Tool to Find My Autoimmune Disease — And It Actually Worked",
        "slug": "open-source-ai-tool-autoimmune-disease-discovery",
        "category": "Research & Analysis",
        "category_id": 8,
        "focus_kw": "AI autoimmune disease detection open source tool",
        "primary_kw": "AI health tool autoimmune disease early detection open source",
        "keywords": ["AI health tool", "autoimmune disease detection", "open source AI", "health AI project", "disease early warning AI"],
        "tools": ["AI health tool", "open source AI"],
        "int_links": [],
        "ext_links": [],
    },
    {
        "id": 214,
        "title": "I Gave AI a $1500/Month Budget for My Small Business — Here Is What Actually Happened",
        "slug": "ai-1500-month-budget-small-business-real-results",
        "category": "Research & Analysis",
        "category_id": 8,
        "focus_kw": "AI business budget $1500 per month small business experiment",
        "primary_kw": "AI budget small business experiment $1500 monthly AI spending results",
        "keywords": ["AI budget small business", "$1500 AI monthly budget", "AI ROI small business", "AI tool spending experiment", "business AI cost benefit"],
        "tools": ["ChatGPT", "Claude", "Copilot", "AI tools"],
        "int_links": [],
        "ext_links": [],
    },
    {
        "id": 215,
        "title": "I Tested AI Music Generators for My Podcast for Three Months — The One That Actually Sounds Human",
        "slug": "ai-music-generators-podcast-3-month-test",
        "category": "Best Picks",
        "category_id": 7,
        "focus_kw": "AI music generator podcast comparison 2026",
        "primary_kw": "AI music generator podcast background music 2026 comparison review",
        "keywords": ["AI music generator podcast", "AI background music podcast", "Sunno alternative", "AI music tool podcast", "AI music generator comparison 2026"],
        "tools": ["Sunno", "Udio", "Suno alternative", "AI music tools"],
        "int_links": [],
        "ext_links": [
            ("Suno", "https://suno.ai"),
            ("Udio", "https://udio.ai"),
        ],
    },
]

# ================================================================
# doi = WP post_id, non-queue serial
# Update: append immediately after publish
# ================================================================
PUBLISHED_ARTICLE_IDS = {
    111, 118, 292, 302, 313, 346, 362, 364, 395, 463, 465, 470,
    501, 507, 553, 556, 634, 640, 646, 652, 970, 1034, 1078, 1086, 1095, 1102, 1110,
    1114, 1120, 1126, 1141, 1162, 1168, 1175, 1176,
    1216, 1229, 1238, 1242, 1247, 1252, 1258, 1262, 1268, 1278, 1291, 1297, 1301, 1304, 1309,
    1315, 1320, 1325, 1330, 1336, 1341, 1348, 1353, 1366, 1370, 1379, 1385, 1389, 1394, 1399,
    1405, 1412, 1417, 1424, 1431, 1436, 1441, 1445, 1449, 1454, 1458, 1543,
    1550, 1558, 1560,
}

# 当前队列：213/214/215已发布（1550/1558/1560），队列空
# WP实际已发布72篇
# 已发布：208→1114, 210→1120, 211→1126, 212→1162, 213→1558, 214→1550, 215→1560
