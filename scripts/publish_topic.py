#!/usr/bin/env python3
"""
publish_topic.py — 从 topics.json 选取选题，通过 regenerate_and_publish.py 完整流水线发布

用法:
  python3 publish_topic.py --id=12          # 发布 topic ID=12
  python3 publish_topic.py --id=12 --dry    # 预览（不发布）
  python3 publish_topic.py --id=4 --id=14   # 批量发布
  python3 publish_topic.py --list           # 列出未发布选题
  python3 publish_topic.py --next          # 发布队列中第一篇

数据流:
  topics.json → article dict（转换字段）→ generate_seo_friendly_content()
             → fix_long_paragraphs() → generate_cover_for_content()
             → _migrate_external_images() → publish_to_wp()（设Tags+封面图+Yoast）
"""

import sys, os, json, time, re, ssl, urllib.request
sys.path.insert(0, os.path.dirname(__file__))

from regenerate_and_publish import (
    generate_seo_friendly_content, fix_long_paragraphs,
    generate_cover_for_content, _migrate_external_images,
    publish_to_wp, precheck_readability, check_narrative_progression,
    _targeted_h2_rewrite, _track_score_milestone, log, xmlrpc_call,
    wp_pre_publish_check,
    WP_USERNAME, WP_APP_PASSWORD, WP_SITE_URL,
)
from shared_config import CATEGORY_MAP


# ============================================================
# topics.json → article dict 字段转换
# ============================================================
def topic_to_article(topic: dict) -> dict:
    """
    将 topics.json 的选题格式转换为 regenerate_and_publish.py 期望的 article dict。
    字段映射关系:
      topics.json          →  article dict
      -------------------------------------------
      id                  →  id
      title               →  title
      keywords            →  keywords (直接复用)
      category            →  category
      category (小写)     →  category_id (通过CATEGORY_MAP映射)
      tools               →  tools (从keywords里提取含工具名的)
      focus_kw            →  focus_kw (取keywords[0])
      narrative_angle      →  (注入到系统提示词上下文中)
    """
    cat = topic.get("category", "")
    # category_id 映射（归一化：小写处理+同义词映射）
    cat_id = _normalize_category(cat)

    # focus_kw：取 keywords[0] 或 keywords 里含工具名的
    keywords = topic.get("keywords", [])
    focus_kw = keywords[0] if keywords else topic.get("title", "")[:60]

    # primary_kw：取 keywords[1] 或 keywords 里含工具名的
    primary_kw = keywords[1] if len(keywords) > 1 else focus_kw

    # tools：尝试从 keywords/narrative_angle 提取工具名（人工维护）
    #        这里做个简单启发式：keywords里含"vs"/"and"/"tool"等词的去掉
    tools = _extract_tools(keywords, topic.get("narrative_angle", ""))

    # 人工补充AI工具名（从标题/关键词中识别）
    tools = _enrich_tools(topic, tools)

    # int_links：自动构建内链（从topics.json中选同分类或同工具的已有选题作为内链目标）
    int_links = _build_int_links(topic)

    article = {
        "id": topic["id"],
        "title": topic["title"],
        "slug": _make_slug(topic["title"]),
        "category": _normalize_category_name(cat),
        "category_id": _normalize_category(cat),
        "focus_kw": focus_kw,
        "primary_kw": primary_kw,
        "keywords": keywords,
        "tools": tools,
        "int_links": int_links,
        "ext_links": [],
        # 附加字段（regenerate_and_publish.py 的函数会读取）
        "narrative_angle": topic.get("narrative_angle", ""),
        "tags": _build_tags(topic),
    }
    return article


def _normalize_category_name(cat: str) -> str:
    """返回规范化后的分类名称（小写→标准名）"""
    synonyms = {
        'research': 'Research & Analysis',
        'comparisons': 'AI Tool Comparisons',
        'best picks': 'Best Picks',
        'tutorials': 'AI Tutorials',
        'free resources': 'Free AI Resources',
    }
    cat_lower = cat.lower().strip()
    return synonyms.get(cat_lower, cat)


def _build_tags(topic: dict) -> list:
    """从 topic keywords 构建 post_tag 标签列表"""
    tags = set()
    for kw in topic.get("keywords", []):
        tag = kw.strip()
        if tag and len(tag) < 40:
            tags.add(tag)
    # 加入分类标签
    cat = topic.get("category", "")
    if cat:
        tags.add(cat)
    return list(tags)[:8]


def _extract_tools(keywords: list, narrative: str = "") -> list:
    """简单启发式：从keywords提取工具名（返回前4个）"""
    # 常见AI工具名列表做过滤
    known_tools = {
        "Cursor", "Claude Code", "Copilot", "GitHub Copilot", "Windsurf",
        "Bolt.new", "Replit Agent", "Lovable", "Perplexity", "ChatGPT",
        "ChatGPT Search", "Gemini", "NotebookLM", "Otter.ai", "Fireflies",
        "Grain", "MacWhisper", "Obsidian", "Notion", "Logseq", "Roam Research",
        "Ollama", "LM Studio", "Sora", "Runway", "Kling", "Ideogram",
        "Recraft", "Midjourney", "DALL-E", "Flux", "Claude", "OpenAI",
        "Mistral", "Llama", "Gemma", "Qwen", "DeepSeek", "Vercel",
        "Supermemory", "Mem.ai", "Cohandidate", "Fabric", "OpenRouter",
        "Jan", "Perry", "Recall", "Goose", "Open Interpreter", "Dify",
    }
    tools = []
    text = " ".join(keywords) + " " + narrative
    for tool in known_tools:
        if tool.lower() in text.lower():
            if tool not in tools:
                tools.append(tool)
    # 如果没匹配到工具名，从前4个keywords中取
    if not tools:
        tools = [kw.strip() for kw in keywords[:4] if kw.strip()]
    return tools[:4]


def _enrich_tools(topic: dict, tools: list) -> list:
    """
    按选题ID人工补充工具名（topics.json的keywords是主题词不是工具名）。
    对于叙事型选题（无明确工具），返回空列表（封面图prompt会用focus_kw代替）。
    """
    # 有明确工具名的选题 → 返回提取结果
    if tools:
        return tools
    # 叙事型选题（无明确工具）→ 返回空，generate_cover_for_content会用focus_kw
    return []


# ============================================================
# 内链缓存（TTL=6小时，减少WP REST API重复调用）
# ============================================================
_INT_LINKS_CACHE = {
    "data": {},      # wp_posts dict
    "fetched_at": 0.0,  # timestamp
}
_CACHE_TTL = 6 * 3600  # 6小时


def _get_wp_posts_cached(ctx) -> dict:
    """获取WP已发布文章列表，带6小时缓存"""
    import time as _time
    now = _time.time()
    if _INT_LINKS_CACHE["data"] and (now - _INT_LINKS_CACHE["fetched_at"]) < _CACHE_TTL:
        return _INT_LINKS_CACHE["data"]
    wp_posts = {}
    for page in range(1, 5):
        try:
            url = (f"https://{{WP_SITE_DOMAIN}}/wp-json/wp/v2/posts"
                   f"?status=publish&per_page=100&page={page}&_fields=id,slug,title")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                raw = resp.read()
            data = json.loads(raw)
            if not data:
                break
            for p in data:
                title_raw = p.get("title", {}).get("rendered", "")
                title = re.sub(r"<[^>]+>", "", title_raw)
                wp_posts[p["id"]] = {"title": title, "slug": p.get("slug", ""), "text": title.lower()}
        except Exception:
            break
    _INT_LINKS_CACHE["data"] = wp_posts
    _INT_LINKS_CACHE["fetched_at"] = now
    return wp_posts


def _build_int_links(topic: dict) -> list:
    """
    为当前文章构建内链列表（基于WP全部已发布文章，不限于topics.json）。
    从WP REST API获取全部已发布文章，对每篇打分后返回top 5。
    带6小时缓存，同一次运行时避免重复API调用。
    """
    if topic.get("published"):
        return []

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # 1. 从缓存获取（6小时TTL）
    wp_posts = _get_wp_posts_cached(ctx)
    if not wp_posts:
        return []

    # 2. 提取当前文章的工具名和关键词
    current_tools_raw = _extract_tools(topic.get("keywords", []), topic.get("narrative_angle", ""))
    current_tools = [t.lower() for t in current_tools_raw]
    current_kw = [kw.lower() for kw in topic.get("keywords", [])]
    current_cat = topic.get("category", "").lower()

    # 3. 分类语义词表（同分类文章标题中通常会出现的词）
    cat_signatures = {
        "best picks":      ["best", "top", "review"],
        "comparisons":     ["vs", "comparison", "showdown", "versus"],
        "research & analysis": ["review", "analysis", "tested", "experiment"],
        "ai tutorials":     ["guide", "how to", "tutorial", "workflow", "setup"],
        "free ai resources":["free", "open source", "no sign-up", "freeware"],
    }
    sig_kws = cat_signatures.get(current_cat, [])

    # 4. BANNED列表（测试/临时文章）
    BANNED = {
        1191,1199,1200,1201,1202,1203,1204,1205,1206,1207,1208,1209,
        1210,1211,1212,1213,1214,1215,1217,1218,1219,1220,1221,1222,
        1223,1224,1225,1226,1227,1228,1230,1231,1232,1233,1234,1235,
        1236,1237,1243,1244,1245,1246
    }

    # 5. 对每篇已发布文章打分
    scored = []
    for pid, info in wp_posts.items():
        if pid in BANNED:
            continue
        score = 0
        text = info["text"]
        title_l = info["title"].lower()

        # 工具名子串匹配（命中即+3）
        if current_tools:
            for tool in current_tools:
                if tool in text:
                    score += 3
                    break

        # 关键词子串匹配（命中即+1）
        for kw in current_kw:
            if kw in text:
                score += 1
                break

        # 分类语义词匹配（+2）
        if any(sw in title_l for sw in sig_kws):
            score += 2

        if score > 0 and info["slug"]:
            scored.append((score, pid, info))

    # 6. 取分数最高的5篇
    scored.sort(key=lambda x: x[0], reverse=True)
    top5 = scored[:5]

    # 7. 构建返回格式（按WP ID去重，同slug只保留1个）
    seen_pids = set()
    result = []
    for score, pid, info in top5:
        if pid in seen_pids:
            continue
        seen_pids.add(pid)
        result.append([info["title"], f"https://{{WP_SITE_DOMAIN}}/{info['slug']}/"])

    return result



def _normalize_category(cat: str) -> int:
    """CATEGORY_MAP兼容：小写处理 + 同义词映射"""
    from shared_config import CATEGORY_MAP
    # 小写归一化
    cat_lower = cat.lower().strip()
    if cat_lower in CATEGORY_MAP:
        return CATEGORY_MAP[cat_lower]
    # 同义词兜底
    synonyms = {
        'research': 'Research & Analysis',
        'comparisons': 'AI Tool Comparisons',
        'best picks': 'Best Picks',
        'tutorials': 'AI Tutorials',
        'free resources': 'Free AI Resources',
    }
    if cat_lower in synonyms:
        return CATEGORY_MAP[synonyms[cat_lower]]
    return CATEGORY_MAP.get(cat, 4)


def _make_slug(title: str) -> str:
    """标题转slug"""
    s = re.sub(r'[^a-z0-9\s]', '', title.lower())
    s = re.sub(r'\s+', '-', s).strip('-')
    return s[:70]


# ============================================================
# 单篇发布
# ============================================================
def publish_single(topic_id: int, dry: bool = False) -> bool:
    """发布单篇 topic，返回是否成功"""
    # 1. 加载 topics.json
    topics_path = os.path.join(os.path.dirname(__file__), "topics", "topics.json")
    with open(topics_path) as f:
        topics = json.load(f)

    topic = None
    for t in topics:
        if t["id"] == topic_id:
            topic = t
            break
    if not topic:
        print(f"❌ 选题 ID={topic_id} 不存在")
        return False

    print("=" * 60)
    print(f"[Topic {topic_id}] {topic['title'][:60]}")
    print(f"  Category: {topic['category']} | Keywords: {topic.get('keywords', [])[:3]}")
    print("=" * 60)

    # 2. 转换为 article dict
    article = topic_to_article(topic)
    print(f"  ✅ article dict 构建完成 (tools={article['tools']})")

    # 3. WP 查重
    wp_check = wp_pre_publish_check(article)
    print(f"  WP已发布: {wp_check['current_count']}篇/{article['category']}")
    if wp_check["duplicate"]:
        dup_parts = []
        if wp_check.get("dup_title"):
            dup_parts.append("标题重复")
        if wp_check.get("dup_fkw"):
            dup_parts.append("FocusKW重复")
        print(f"  ❌ 选题重复: {'+'.join(dup_parts)}，跳过")
        return False

    # 4. 生成文章
    print("  生成文章中...")
    content = generate_seo_friendly_content(article)
    content = fix_long_paragraphs(content)

    # 5. 预检
    check = precheck_readability(content, article)
    print(f"  预检: {check['total_words']}词 | H2:{check['h2_count']} | AI腔:{len(check['robotic_phrases'])}")
    np_check = check_narrative_progression(content)
    print(f"  叙事: {np_check['verdict']} | 模板H2违规:{np_check['fail_types']}/{np_check['total']}")

    # 6. H2违规定向重写
    h2_fail = [i for i in check["issues"] if "[FAIL]" in i and "H2模板结构违规" in i]
    if h2_fail or np_check["verdict"] == "FAIL":
        print("  ⚠️ H2违规/叙事FAIL，执行定向重写...")
        content = _targeted_h2_rewrite(article, content, np_check)
        content = fix_long_paragraphs(content)
        check2 = precheck_readability(content, article)
        np_check2 = check_narrative_progression(content)
        _track_score_milestone(article["id"], article["title"], np_check, np_check2, 1)
        print(f"  重写后: {check2['total_words']}词 | 叙事:{np_check2['verdict']}")

    if dry:
        print("\n--- Preview (800 chars) ---")
        print(content[:800])
        print("---END---")
        return True

    # 7. 生成封面图 + 迁移正文外部图片
    print("  生成封面图 + 迁移外部图片...")
    content, featured_wp_url = generate_cover_for_content(article, content)
    if featured_wp_url:
        print(f"  封面图: {featured_wp_url[:60]}")
    else:
        print("  ⚠️ 封面图生成失败，保留占位符")

    # 8. 迁移正文外部图片（图片到WP媒体库）
    print("  迁移正文外部图片...")
    content, migrated, failed = _migrate_external_images(content)
    print(f"  图片迁移: {migrated}张成功, {failed}张失败")
    print(f"  正文长度: {len(content.split())}词")

    # 9. 发布到WP（设Tags+封面图+Yoast）
    print("  发布到WP...")
    ok = _recheck_and_publish(article, content, featured_wp_url)
    if ok:
        print(f"  ✅ 发布成功: Topic {topic_id}")
        # 标记 topics.json 为已发布（不标记published，避免误覆盖真实已发布状态）
        _mark_topic_published(topic_id, ok)
    else:
        print(f"  ❌ 发布失败: Topic {topic_id}")
    return ok


def _recheck_and_publish(article: dict, content: str, featured_wp_url: str) -> bool:
    """最终复查 + publish_to_wp"""
    # 合并到 publish_to_wp（publish_to_wp内部已做预检）
    ok = publish_to_wp(article, content, featured_wp_url)
    if not ok:
        return False

    # WP REST API POST后标签可能未设上，再用XML-RPC补一次
    tags = article.get("tags", _build_tags(article))
    if tags:
        try:
            # 获取刚发布的post_id（从WP最新一篇推断，这里简化处理）
            # publish_to_wp返回True但不返回post_id，用REST API查
            import urllib.request, ssl, base64
            ctx = ssl.create_default_context()
            auth = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
            hdrs = {"Authorization": f"Basic {auth}", "User-Agent": "Mozilla/5.0"}
            req = urllib.request.Request(
                f"{WP_SITE_URL}/wp-json/wp/v2/posts?per_page=1&status=publish",
                headers=hdrs
            )
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                posts = json.loads(resp.read())
                if posts:
                    latest_id = posts[0]["id"]
                    from regenerate_and_publish import _set_wp_post_tags
                    _set_wp_post_tags(latest_id, tags)
                    print(f"  ✅ Tags补设成功: {tags[:3]}")
        except Exception as e:
            print(f"  ⚠️ Tags补设失败: {e}")
    return True


def _mark_topic_published(topic_id: int, ok: bool):
    """更新 topics.json 标记 published（仅在发布成功时）"""
    if not ok:
        return
    topics_path = os.path.join(os.path.dirname(__file__), "topics", "topics.json")
    with open(topics_path) as f:
        topics = json.load(f)
    for t in topics:
        if t["id"] == topic_id:
            t["published"] = True
            t["published_at"] = time.strftime("%Y-%m-%d")
            break
    with open(topics_path, "w") as f:
        json.dump(topics, f, ensure_ascii=False, indent=2)
    print(f"  topics.json 标记 published=True")


# ============================================================
# 批量 / 列表
# ============================================================
def list_unpublished():
    """列出未发布的选题"""
    topics_path = os.path.join(os.path.dirname(__file__), "topics", "topics.json")
    with open(topics_path) as f:
        topics = json.load(f)
    unpublished = [t for t in topics if not t.get("published")]
    print(f"\n📋 未发布选题 ({len(unpublished)}/{len(topics)})")
    print("-" * 60)
    for t in unpublished:
        cat = t.get("category", "")
        print(f"  [{t['id']:2d}] {cat:20s} {t['title'][:55]}")
    print()


def publish_next():
    """发布队列中第一篇未发布的"""
    topics_path = os.path.join(os.path.dirname(__file__), "topics", "topics.json")
    with open(topics_path) as f:
        topics = json.load(f)
    for t in topics:
        if not t.get("published"):
            publish_single(t["id"])
            return
    print("✅ 全部已发布！")


def main():
    ids = []
    dry = False
    list_mode = False
    next_mode = False

    for arg in sys.argv[1:]:
        if arg == "--list":
            list_mode = True
        elif arg == "--next":
            next_mode = True
        elif arg == "--dry":
            dry = True
        elif arg.startswith("--id="):
            ids.append(int(arg.split("=", 1)[1]))
        elif arg.startswith("--"):
            print(f"未知参数: {arg}")

    if list_mode:
        list_unpublished()
    elif next_mode:
        publish_next()
    elif ids:
        for tid in ids:
            ok = publish_single(tid, dry=dry)
            time.sleep(5)
            if not ok:
                break
    else:
        print("用法:")
        print("  python3 publish_topic.py --id=12           # 发布选题12")
        print("  python3 publish_topic.py --id=12 --dry     # 预览选题12")
        print("  python3 publish_topic.py --id=4 --id=14    # 批量发布")
        print("  python3 publish_topic.py --list            # 列出未发布选题")
        print("  python3 publish_topic.py --next            # 发布队列中下一篇")


if __name__ == "__main__":
    main()