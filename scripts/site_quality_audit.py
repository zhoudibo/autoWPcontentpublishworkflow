#!/usr/bin/env python3
"""
site_quality_audit.py
=====================
全站文章质量审计脚本。
用法：
  python3 site_quality_audit.py              # 全站审计，输出报告
  python3 site_quality_audit.py --fix       # 审计 + 自动修复
  python3 site_quality_audit.py --fix --id 1176  # 只审计/修复指定文章

博哥双绿标准：
  ✅ SEO绿：Focus Keyword设置 + 关键词1%-1.5%密度自然分布
  ✅ Readability绿：单句≤20词、单段落≤3句、每100词≥1过渡词、被动语态≤10%
  ✅ 无AI腔（29种模式）
  ✅ 内链≥2个
  ✅ 外链有 nofollow
  ✅ 图片ALT完整
  ✅ 叙事H2（禁止模板H2）
  ✅ 字数≥1000词
"""

import os, sys, re, html as html_module, json, time, ssl, base64, argparse, http.client
import urllib.request, urllib.parse
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from shared_config import WP_USERNAME, WP_APP_PASSWORD

WP_URL = "https://{{WP_SITE_DOMAIN}}"
AUTH = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
HDRS = {
    "Content-Type": "application/json",
    "Authorization": f"Basic {AUTH}",
    "User-Agent": "Mozilla/5.0 WP-Audit/1.0",
}
CTX = ssl.create_default_context()

# ─── AI腔词汇表（扩展版，来源：humanizer patterns） ────────────────
AI_PHRASES = {
    # 1. 虚假升华
    "in today's rapidly evolving": "in recent years",
    "rapidly evolving landscape": "changing field",
    "evolving landscape of": "field of",
    "stands as a testament": "",
    "serves as a testament": "",
    "a testament to": "evidence of",
    "a vital role": "an important role",
    "plays a vital role": "plays an important role",
    "underscores the importance": "shows the importance",
    "highlighting its importance": "showing its importance",
    "reflecting broader trends": "",
    "symbolizing": "",
    "setting the stage for": "",
    "marking a significant": "marking a",
    "represents a shift": "marks a change",
    "key turning point": "",
    "deeply rooted": "",
    # 2. 促销/广告语言
    "boasts a": "has a",
    "vibrant": "",
    "breathtaking": "",
    "renowned": "",
    "groundbreaking": "new",
    "state-of-the-art": "modern",
    "cutting-edge": "advanced",
    "best-in-class": "high-quality",
    "next-generation": "new",
    "unparalleled": "exceptional",
    "top-tier": "premium",
    "game-changing": "useful",
    "revolutionize": "improve",
    "ultimate solution": "best option",
    "nestled in the heart of": "in",
    "in the heart of": "in",
    "must-visit": "",
    # 3. 过度强调媒体/知名度
    "leading expert": "",
    "active social media presence": "",
    "independent coverage": "",
    "widely regarded": "",
    # 4. 表面化 -ing
    "leveraging": "using",
    "harnessing the potential": "using",
    "fostering": "supporting",
    "cultivating": "building",
    "encompassing": "including",
    "showcasing": "showing",
    "contributing to": "",
    "ensuring": "",
    # 5. 模糊归因
    "industry reports indicate": "",
    "observers have cited": "",
    "experts argue": "",
    "some critics argue": "",
    "several sources suggest": "",
    # 6. 模板章节
    "despite its challenges": "",
    "despite these challenges": "",
    "future outlook": "",
    "challenges and legacy": "",
    # 7. AI高频词
    "delve into": "explore",
    "navigate the": "navigate",
    "master the art of": "learn to",
    "the realm of": "the field of",
    "play a pivotal role": "play an important role",
    "takes center stage": "",
    "emerges as a": "is a",
    "stands out from the crowd": "",
    "it is worth noting": "notably",
    "on the other hand": "",
    "additionally, it is": "it also",
    "thus, it is": "so it is",
    "it should be noted": "notably",
    "needless to say": "obviously",
    "last but not least": "",
    "fast forward to": "",
    "when it comes to": "for",
    "in terms of": "in",
    "at the end of the day": "",
    "what's more": "also",
    "moreover": "also",
    "furthermore": "also",
    "ultimately": "",
    "in conclusion": "",
    "the secret to": "",
    "unlock the power of": "",
    "harness the potential of": "",
    "it is no secret that": "",
    "there are many ways to": "there are ways to",
    "there are several factors": "",
    "artificial intelligence is": "AI tools are",
    "ai technology is": "AI tools are",
    "ai-powered": "AI-driven",
    "in today's digital age": "today",
    "it's important to note": "notably",
    "it is important to note": "notably",
    "it is clear that": "it is evident that",
    "there is no doubt": "there is little doubt",
    "machine learning enables": "machine learning helps",
    "deep learning is": "deep learning has",
    "tapestry of": "",
    "intricacies of": "details of",
    "landscape of ai": "AI field",
    "the world of ai": "the AI industry",
    # 8. 简单系动词回避
    "serves as": "is",
    "stands as": "is",
    "features": "has",
    "offers": "has",
    # 9. 负面平行/尾随否定
    "not only does it": "it does",
    "no guessing": "",
    "no wasted motion": "",
    # 12. 虚假范围
    "from the big bang to": "",
    "from x to y and from a to b": "",
    # 14. 破折号滥用（记录但不自动修）
    "—": " — ",
    # 15. 粗体（记录）
    # 16. 列表标题（记录）
    # 17. Title Case（记录）
    # 18. Emoji（记录）
    "🚀": "", "💡": "", "✅": "", "❌": "", "⚠️": "",
    "🔥": "", "⭐": "", "🎯": "", "📌": "",
    # 21. 知识截止免责
    "as of my last training": "",
    "up to my last training update": "",
    "while specific details are limited": "",
    "based on available information": "",
    "as of your knowledge cutoff": "",
    # 22. 谄媚语气
    "great question": "",
    "you're absolutely right": "",
    "excellent point": "",
    # 23. 填充短语
    "in order to": "to",
    "due to the fact that": "because",
    "at this point in time": "now",
    "in the event that": "if",
    "the system has the ability to": "the system can",
    # 25. 泛化结论
    "the future looks bright": "",
    "exciting times lie ahead": "",
    "major step in the right direction": "",
    # 26. 连字符滥用（不一致使用即可）
    # 27. 说服性权威套话
    "the real question is": "",
    "at its core": "",
    "in reality": "",
    "what really matters": "",
    "fundamentally": "",
    "the deeper issue": "",
    "the heart of the matter": "",
    # 28. 引导宣告
    "let's dive in": "",
    "let's explore": "",
    "let's break this down": "",
    "here's what you need to know": "",
    "without further ado": "",
    # 29. 碎片化标题
    # 额外常见AI腔
    "comprehensive guide": "guide",
    "this article will explore": "",
    "it is crucial to": "it is important to",
    "it is essential to": "it is important to",
    "a wide range of": "many",
    "a multitude of": "many",
    "in a world where": "where",
    "as technology continues to": "as technology",
}

# 叙事型H2引导词（H2第一句不应出现这些则FAIL）
NARRATIVE_H2_TRIGGERS = [
    "this article", "in this article", "in this guide", "in this post",
    "we will explore", "we will discuss", "this section covers",
    "introduction", "overview", "what is", "getting started",
    "key features", "features include", "pros and cons",
    "how to choose", "how do you", "the benefits of",
    "the bottom line", "verdict", "summary",
]

def urlopen_retry(url, data=None, headers=None, method="GET", max_retries=4):
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc
    path = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
    extra = headers or {}
    for attempt in range(max_retries):
        conn = None
        try:
            conn = http.client.HTTPSConnection(host, context=CTX, timeout=60)
            body = data if data else None
            hdrs = {**HDRS, **extra}
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            raw = resp.read()
            conn.close()
            return json.loads(raw)
        except Exception as e:
            if conn:
                try: conn.close()
                except: pass
            wait = 15 * (attempt + 1)
            print(f"    ⚠️ Attempt {attempt+1}/{max_retries} failed: {type(e).__name__}, retry in {wait}s...")
            time.sleep(wait)
    raise RuntimeError(f"All {max_retries} attempts failed for {url}")

def get_wp_posts(per_page=100, offset=0, status="publish"):
    """获取WP文章列表"""
    url = f"{WP_URL}/wp-json/wp/v2/posts?per_page={per_page}&offset={offset}&status={status}&orderby=date&order=asc"
    return urlopen_retry(url, headers=HDRS)

def get_wp_post_content(post_id):
    """获取单篇文章完整数据"""
    url = f"{WP_URL}/wp-json/wp/v2/posts/{post_id}?_fields=id,title,slug,content,meta,date,categories,tags"
    return urlopen_retry(url, headers=HDRS)

def strip_html(html_text):
    """移除HTML标签，返回纯文本"""
    text = re.sub(r'<img[^>]+alt="([^"]*)"[^>]*>', r'\1 ', html_text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'&#\d+;', ' ', text)
    text = re.sub(r'&[a-z]+;', ' ', text)
    return html_module.unescape(text).strip()

def check_article(post_id, post_data):
    """对单篇文章进行全面质量检查"""
    title = post_data["title"]["rendered"]
    raw_html = post_data["content"]["rendered"]
    text = strip_html(raw_html)
    text_lower = text.lower()

    issues = []
    auto_fixes = {}
    warnings = []

    # ── 1. 字数检查 ──────────────────────────────
    words = [w for w in re.findall(r'\b[a-z]+\b', text_lower) if len(w) > 2]
    word_count = len(words)
    if word_count < 1000:
        issues.append(f"[FAIL] 字数不足({word_count}词, 目标≥1000词)")
    elif word_count < 1400:
        warnings.append(f"[WARN] 字数偏少({word_count}词, 建议≥1400词)")

    # ── 2. H2结构检查 ─────────────────────────────
    h2s = re.findall(r'<h2[^>]*>([^<]+)</h2>', raw_html, re.I)
    h2_texts = [html_module.unescape(h).strip() for h in h2s]
    h2_count = len(h2s)
    narrative_h2_count = 0
    template_h2_count = 0
    for h2 in h2_texts:
        h2_l = h2.lower()
        is_narrative = not any(t in h2_l for t in NARRATIVE_H2_TRIGGERS)
        if is_narrative:
            narrative_h2_count += 1
        else:
            template_h2_count += 1
    if h2_count == 0:
        issues.append("[FAIL] 文章无H2章节结构")
    elif template_h2_count > 1:
        issues.append(f"[FAIL] 模板H2过多({template_h2_count}个, 应≤1个): {[h for h in h2_texts if h.lower() in [t for t in NARRATIVE_H2_TRIGGERS]]}")
    elif narrative_h2_count < 2:
        warnings.append(f"[WARN] 叙事H2不足({narrative_h2_count}个, 目标≥2个)")

    # ── 3. AI腔检测 ──────────────────────────────
    found_ai_phrases = {}
    for phrase, replacement in AI_PHRASES.items():
        if phrase.lower() in text_lower:
            count = text_lower.count(phrase.lower())
            found_ai_phrases[phrase] = count
    ai_phrase_count = sum(found_ai_phrases.values())

    # 检测句长均匀性（CV < 0.25 = AI腔特征）
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip() and len(s.split()) > 3]
    if len(sentences) >= 10:
        import statistics as _s
        wc = [len(s.split()) for s in sentences]
        mean_wc = _s.mean(wc)
        std_wc = _s.stdev(wc) if len(wc) > 1 else 0
        cv = std_wc / mean_wc if mean_wc > 0 else 1
        if cv < 0.25:
            warnings.append(f"[WARN] 句长过度均匀(CV={cv:.2f}, 人类写作CV通常>0.25)")
    if ai_phrase_count >= 10:
        issues.append(f"[FAIL] AI腔词汇过多({ai_phrase_count}处)")
        auto_fixes["ai_phrases"] = found_ai_phrases
    elif ai_phrase_count >= 3:
        warnings.append(f"[WARN] AI腔词汇({ai_phrase_count}处)")
        auto_fixes["ai_phrases"] = found_ai_phrases

    # ── 4. 可读性检查 ─────────────────────────────
    long_sentences = [s for s in sentences if len(s.split()) > 25]
    if len(long_sentences) > 5:
        warnings.append(f"[WARN] 长句过多({len(long_sentences)}句>25词)")

    # ── 5. 图片ALT检查 ────────────────────────────
    imgs = re.findall(r'<img[^>]+>', raw_html, re.I)
    alt_issues = 0
    for img in imgs:
        has_alt = bool(re.search(r'alt=["\']([^"\']+)["\']', img, re.I))
        alt_empty = bool(re.search(r'alt=["\']\s*["\']', img, re.I))
        if not has_alt or alt_empty:
            alt_issues += 1
    if alt_issues > 0:
        auto_fixes["alt_issues"] = alt_issues
        warnings.append(f"[INFO] 图片ALT缺失({alt_issues}张)")

    # ── 6. 内链检查 ──────────────────────────────
    internal_links = re.findall(r'<a[^>]+href=["\']([^"\']*)["\'][^>]*>([^<]+)</a>', raw_html, re.I)
    int_link_count = sum(1 for url, text in internal_links if "{{WP_SITE_DOMAIN}}" in url)
    if int_link_count < 2:
        warnings.append(f"[WARN] 内链不足({int_link_count}个, 建议≥2个)")

    # ── 7. 外链nofollow检查 ──────────────────────
    ext_links = re.findall(r'<a[^>]+href=["\']([^"\']*)["\'][^>]*>([^<]+)</a>', raw_html, re.I)
    nofollow_issues = 0
    for url, anchor in ext_links:
        if "{{WP_SITE_DOMAIN}}" not in url:
            if "nofollow" not in url.lower():
                nofollow_issues += 1
    if nofollow_issues > 0:
        auto_fixes["nofollow_issues"] = nofollow_issues
        warnings.append(f"[INFO] 外部链接缺nofollow({nofollow_issues}处)")

    # ── 8. Yoast SEO检查 ──────────────────────────
    # focus_kw 在 meta 中查找（通过 REST API custom_fields 不可直接读）
    # 用正文预检方式间接判断
    # 这里主要标记，没有 auto_fix
    if word_count >= 1000 and narrative_h2_count >= 2 and ai_phrase_count < 5:
        seo_score = "GOOD"
    elif word_count >= 800 and ai_phrase_count < 10:
        seo_score = "FAIR"
    else:
        seo_score = "POOR"

    # ── 汇总 ──────────────────────────────
    total_fail = len([i for i in issues if i.startswith("[FAIL]")])
    total_warn = len(warnings)

    return {
        "post_id": post_id,
        "title": title,
        "slug": post_data["slug"],
        "word_count": word_count,
        "h2_count": h2_count,
        "narrative_h2": narrative_h2_count,
        "template_h2": template_h2_count,
        "ai_phrases": ai_phrase_count,
        "ai_detail": found_ai_phrases,
        "long_sentences": len(long_sentences),
        "alt_issues": alt_issues,
        "int_links": int_link_count,
        "nofollow_issues": nofollow_issues,
        "issues": issues,
        "warnings": warnings,
        "auto_fixes": auto_fixes,
        "seo_score": seo_score,
        "total_fail": total_fail,
        "total_warn": total_warn,
        "priority": max(total_fail * 10 + total_warn, 1),
    }


def apply_auto_fixes(raw_html, check_result, focus_kw="ai tool"):
    """应用可自动修复的项"""
    html = raw_html
    fixed = []

    # 1. AI腔词汇替换
    if "ai_phrases" in check_result["auto_fixes"]:
        for phrase, count in check_result["ai_detail"].items():
            if phrase in AI_PHRASES and AI_PHRASES[phrase]:
                replacement = AI_PHRASES[phrase]
                pattern = re.compile(re.escape(phrase), re.IGNORECASE)
                new_html = pattern.sub(replacement, html)
                if new_html != html:
                    fixed.append(f"AI腔: '{phrase}' → '{replacement}'")
                    html = new_html

    # 2. 图片ALT补全
    if "alt_issues" in check_result["auto_fixes"]:
        alt_text = focus_kw or "AI tool"
        def fix_alt(m):
            tag = m.group(0)
            has_valid = bool(re.search(r'alt=["\']([^"\']+)["\']', tag, re.I)) and not re.search(r'alt=["\']\s*["\']', tag, re.I)
            if not has_valid:
                tag = re.sub(r'(<img\b)', rf'\1 alt="{alt_text}"', tag)
            return tag
        new_html = re.sub(r'<img[^>]+>', fix_alt, html, flags=re.I)
        if new_html != html:
            fixed.append(f"图片ALT补全({check_result['alt_issues']}张)")
            html = new_html

    # 3. 外链nofollow
    if "nofollow_issues" in check_result["auto_fixes"]:
        def add_nofollow(m):
            tag = m.group(0)
            href_m = re.search(r'href="([^"]*)"', tag)
            if not href_m or "{{WP_SITE_DOMAIN}}" in href_m.group(1):
                return tag
            if "rel=" in tag:
                if "nofollow" not in tag:
                    tag = re.sub(r'rel="([^"]*)"', r'rel="\1 nofollow noopener"', tag)
            else:
                tag = tag.replace(">", ' rel="nofollow noopener">', 1)
            return tag
        new_html = re.sub(r'<a\b[^>]+>', add_nofollow, html)
        if new_html != html:
            fixed.append(f"外链nofollow({check_result['nofollow_issues']}处)")
            html = new_html

    return html, fixed


def update_wp_post(post_id, html_content):
    """通过REST API更新文章内容"""
    payload = json.dumps({"content": html_content}).encode()
    return urlopen_retry(
        f"{WP_URL}/wp-json/wp/v2/posts/{post_id}",
        data=payload,
        headers=HDRS,
        method="POST",
    )


def main():
    parser = argparse.ArgumentParser(description="全站文章质量审计")
    parser.add_argument("--fix", action="store_true", help="执行自动修复")
    parser.add_argument("--id", type=int, help="只审计指定文章ID")
    parser.add_argument("--min-priority", type=int, default=0, help="最小优先级阈值")
    parser.add_argument("--limit", type=int, default=999, help="最多审计文章数")
    args = parser.parse_args()

    print("=" * 65)
    print("  全站文章质量审计")
    print("=" * 65)

    # 获取文章列表
    print("\n[1/4] 获取文章列表...")
    all_posts = []
    offset = 0
    per_page = 100
    while True:
        posts = get_wp_posts(per_page=per_page, offset=offset)
        if not posts:
            break
        all_posts.extend(posts)
        if len(posts) < per_page:
            break
        offset += per_page
        print(f"  已获取 {len(all_posts)} 篇...")
        time.sleep(1)
    print(f"  共获取 {len(all_posts)} 篇已发布文章")

    # 过滤
    if args.id:
        all_posts = [p for p in all_posts if p["id"] == args.id]
        print(f"  限定ID={args.id}")

    all_posts = all_posts[: args.limit]

    # 逐篇审计
    print(f"\n[2/4] 审计 {len(all_posts)} 篇文章...")
    results = []
    for i, post in enumerate(all_posts):
        pid = post["id"]
        print(f"  [{i+1}/{len(all_posts)}] 审计 ID={pid}...", end=" ", flush=True)
        try:
            full_post = get_wp_post_content(pid)
            result = check_article(pid, full_post)
            results.append(result)
            status_icon = "🔴" if result["total_fail"] > 0 else ("🟡" if result["total_warn"] > 0 else "🟢")
            print(f"{status_icon} fail={result['total_fail']} warn={result['total_warn']} words={result['word_count']} AI腔={result['ai_phrases']} H2={result['h2_count']}(n={result['narrative_h2']})")
        except Exception as e:
            print(f"❌ Error: {e}")
            results.append({
                "post_id": pid, "title": post.get("title", {}).get("rendered", ""),
                "slug": post.get("slug", ""),
                "error": str(e),
                "total_fail": 999, "priority": 9999,
            })
        time.sleep(0.5)

    # 排序（按优先级）
    results.sort(key=lambda x: x.get("priority", 0), reverse=True)

    # 输出报告
    print("\n" + "=" * 65)
    print("  审计报告")
    print("=" * 65)

    fail_results = [r for r in results if r.get("total_fail", 0) > 0]
    warn_only = [r for r in results if r.get("total_fail", 0) == 0 and r.get("total_warn", 0) > 0]
    clean = [r for r in results if r.get("total_fail", 0) == 0 and r.get("total_warn", 0) == 0 and not r.get("error")]

    print(f"\n汇总: 🔴失败={len(fail_results)} | 🟡警告={len(warn_only)} | 🟢通过={len(clean)}")
    print(f"总失败项: {sum(r.get('total_fail',0) for r in fail_results)}")
    print(f"总警告项: {sum(r.get('total_warn',0) for r in results)}")

    print(f"\n── 🔴 失败项详情 ({len(fail_results)}篇) ──")
    for r in fail_results:
        print(f"\n  ID={r['post_id']} | {r['title'][:55]}")
        print(f"    SEO评分: {r['seo_score']} | 字数:{r['word_count']} | H2:{r['h2_count']}(n={r['narrative_h2']}/t={r['template_h2']}) | AI腔:{r['ai_phrases']}处")
        for issue in r.get("issues", []):
            print(f"    ❌ {issue}")
        for warn in r.get("warnings", []):
            print(f"    ⚠️  {warn}")

    print(f"\n── 🟡 仅警告项 ({len(warn_only)}篇) ──")
    for r in warn_only[:10]:
        print(f"  ID={r['post_id']} | {r['title'][:50]}")
        for warn in r.get("warnings", []):
            print(f"    ⚠️  {warn}")

    # 自动修复
    if args.fix and fail_results:
        print(f"\n[3/4] 执行自动修复...")
        fix_count = 0
        for r in fail_results:
            if not r.get("auto_fixes"):
                continue
            pid = r["post_id"]
            print(f"\n  修复 ID={pid}: {r['title'][:50]}")
            print(f"    auto_fixes: {list(r['auto_fixes'].keys())}")
            try:
                full_post = get_wp_post_content(pid)
                raw_html = full_post["content"]["rendered"]
                new_html, fixed = apply_auto_fixes(raw_html, r)
                if fixed:
                    update_wp_post(pid, new_html)
                    print(f"    ✅ 修复成功: {' | '.join(fixed)}")
                    fix_count += 1
                else:
                    print(f"    ⏭️  无需修复")
            except Exception as e:
                print(f"    ❌ 修复失败: {e}")
            time.sleep(2)

        print(f"\n[4/4] 修复完成: {fix_count}/{len(fail_results)} 篇已处理")

        # 重新审计（抽查前3篇）
        print("\n── 修复后复检 ──")
        for r in fail_results[:3]:
            pid = r["post_id"]
            try:
                full_post = get_wp_post_content(pid)
                new_result = check_article(pid, full_post)
                icon = "🟢" if new_result["total_fail"] == 0 else "🔴"
                print(f"  ID={pid} {icon} fail={new_result['total_fail']} warn={new_result['total_warn']} AI腔={new_result['ai_phrases']}")
            except Exception as e:
                print(f"  ID={pid} ❌ {e}")

    print("\n" + "=" * 65)
    print("Done.")
    print("=" * 65)


if __name__ == "__main__":
    main()
