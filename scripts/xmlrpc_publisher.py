#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MiniMax AI Tools Auto-Publisher v4.5
Full workflow: Generate -> Clean -> Self-check -> Featured image (permanent) -> Publish -> Validate

Usage:
  python3 xmlrpc_publisher.py --dry-run       # preview first article
  python3 xmlrpc_publisher.py --execute       # publish all
  python3 xmlrpc_publisher.py --daily-one     # publish 1 balanced article (cron mode)
"""

import os, sys, json, time, random, re, html as html_mod, urllib.request, urllib.error, xmlrpc.client, ssl, requests
from datetime import datetime, timezone
from typing import Optional

# ============================================================
# SHARED CONFIG (single source of truth)
# ============================================================
sys.path.insert(0, os.path.dirname(__file__))
from shared_config import (
    MINIMAX_API_KEY, MINIMAX_CHAT_MODEL, WP_SITE_URL, WP_APP_PASSWORD,
    CATEGORY_MAP, CATEGORY_SLUGS, ARTICLES, PUBLISHED_ARTICLE_IDS,
    SYSTEM_PROMPT, ARTICLE_PROMPT_TEMPLATE, fix_long_paragraphs
)
WP_USERNAME = '{{WP_USERNAME}}'  # XML-RPC auth: author=2


# ================================================================
# 站内链接匹配（域名从环境变量动态读取）
# ================================================================
def _int_link_pattern():
    """构建站内链接正则（不硬编码任何域名）"""
    try:
        import sys as _s, pathlib as _p
        _s.path.insert(0, str(_p.Path(__file__).resolve().parent.parent / "config"))
        from config import WP_SITE_DOMAIN  # type: ignore
        domain = re.escape(WP_SITE_DOMAIN)
    except Exception:
        domain = r"[^/]+\.[a-z]{2,}"
    return r'<a\s+href="https?://' + domain + r'[^"]*"'


_INT_LINK_RE = _int_link_pattern()


def log(msg: str, level: str = "INFO"):
    """日志输出"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")


def call_minimax_chat(messages: list, model: str = None, temperature: float = 0.7, max_tokens: int = 4096) -> str:
    """
    调用MiniMax Chat API
    """
    if model is None:
        model = MINIMAX_CHAT_MODEL
    endpoint = "https://api.minimaxi.com/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
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
        
        # MiniMax M2.7：内容可能在 reasoning_content 而不是 content
        msg = resp_data["choices"][0]["message"]
        content = msg.get("content", "") or msg.get("reasoning_content", "")
        if not content:
            log(f"警告: content 和 reasoning_content 均为空，响应: {str(resp_data)[:200]}", "WARNING")
        return content.strip()
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        log(f"HTTP错误 {e.code}: {error_body[:500]}", "ERROR")
        raise
    except Exception as e:
        log(f"API调用异常: {str(e)}", "ERROR")
        raise e


def generate_tag_description(tag_name: str) -> dict:
    """
    用 MiniMax 生成高质量 tag SEO 描述（description + meta description + focus keyword）
    返回 dict: {desc, metadesc, focus_kw}

    MiniMax M2.7 不遵守 XML/JSON 结构化格式，必须用 Few-shot 示例让模型模仿输出。
    """
    prompt = f"""Write tag SEO metadata for BestAIToolsSite.com.

Example 1 — Tag: "ChatGPT alternatives"
Description: Explore the best ChatGPT alternatives available in 2026. Find AI chatbots with better features, lower costs, and unique capabilities for content creation, coding, and research. Read honest comparisons and find your perfect AI assistant.
Meta: Best ChatGPT Alternatives 2026: free and paid options compared. Find AI chatbots with better features and pricing for any use case.
KW: ChatGPT alternatives

Example 2 — Tag: "Free AI tools"
Description: Discover the best free AI tools for students and content creators. This collection covers no-cost grammar checkers, image generators, writing assistants, and research tools. Save money while boosting productivity with these tested free options.
Meta: Free AI Tools 2026: best no-cost options for students and creators. Grammar checkers, image generators, writing aids and more reviewed.
KW: Free AI tools

Now write for this tag: "{tag_name}"
Description:"""

    messages = [{"role": "user", "content": prompt}]
    response = call_minimax_chat(messages, temperature=0.5, max_tokens=512)

    # 清洗思考过程
    response = re.sub(r'<think>[\s\S]*?', '', response).strip()

    # 按空行解析：Description / Meta / KW 三段
    parts = [p.strip() for p in response.split('\n\n') if p.strip()]
    if len(parts) >= 3:
        desc, metadesc, focus_kw = parts[0], parts[1], parts[2]
    elif len(parts) == 2:
        desc, metadesc, focus_kw = parts[0], parts[1], tag_name
    else:
        # 回退：按换行符拆分
        lines = [l.strip() for l in response.split('\n') if l.strip()]
        desc = lines[0] if lines else f"{tag_name} guide with expert reviews, comparisons, and step-by-step tutorials."
        metadesc = lines[1] if len(lines) > 1 else f"Explore the best {tag_name} tools. Expert reviews, comparisons, and pricing guides for content creators."
        focus_kw = lines[2] if len(lines) > 2 else tag_name

    if len(metadesc) > 155:
        metadesc = metadesc[:152] + "..."
    if len(desc) > 150:
        desc = desc[:147] + "..."

    return {"desc": desc, "metadesc": metadesc, "focus_kw": focus_kw}



def _resolve_tag_ids(tag_names):
    """把 tag 名称字符串转为已有 term_id 列表；不存在则通过 REST API 自动创建（带 Yoast SEO 字段）"""
    _ensure_tag_cache()
    ids = []
    missing = []
    for name in tag_names:
        name_clean = name.strip()
        tid = _wp_tag_cache.get(name_clean.lower())
        if tid:
            ids.append(tid)
        else:
            missing.append(name_clean)

    # 自动创建不存在的标签（通过 REST API，带 Yoast SEO 字段）
    for name in missing:
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

        # 用 MiniMax 生成真实的 SEO 描述
        tag_seo = generate_tag_description(name)
        desc = tag_seo["desc"]
        metadesc = tag_seo["metadesc"]
        focus_kw = tag_seo["focus_kw"]

        payload = {
            "name": name,
            "slug": slug,
            "description": desc,
            "focus_kw": focus_kw,
            "metadesc": metadesc,
        }

        try:
            resp = _rest_api_call("POST", "/wp-json/ai-tools/v1/create-tag", payload)
            if resp and isinstance(resp, dict) and "term_id" in resp:
                tid = int(resp["term_id"])
                _wp_tag_cache[name.lower()] = tid
                ids.append(tid)
                log(f"Created tag with Yoast SEO: '{name}' → ID={tid}", "OK")
            else:
                log(f"Failed to create tag '{name}': unexpected resp {resp}", "WARN")
        except Exception as e:
            log(f"Failed to create tag '{name}': {e}", "WARN")

        time.sleep(1)  # 避免 Hostinger ModSecurity 触发

    return ids


def _rest_api_call(method, path, data=None):
    """通过 WordPress REST API (Basic Auth) 发送请求"""
    import requests as _req
    url = f"{WP_SITE_URL}{path}"
    kwargs = {"auth": requests.auth.HTTPBasicAuth(WP_USERNAME, WP_APP_PASSWORD),
              "headers": {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
              "timeout": 30}
    if data is not None:
        kwargs["json"] = data
    resp = _req.request(method, url, **kwargs)
    if resp.status_code >= 400:
        return None
    return resp.json()

# ============================================================
# TRIGGER YOAST SAVE via admin-ajax.php (forces Yoast to re-analyze)
# ============================================================
def _trigger_yoast_save(post_id):
    """通过 WordPress 内部 AJAX 端点强制触发 Yoast SEO save_post 钩子"""
    try:
        # 获取 WP nonce
        ctx = ssl.create_default_context()
        ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        nonce_req = urllib.request.Request(
            f"{WP_SITE_URL}/wp-admin/admin-ajax.php?action=rest-nonce",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(nonce_req, context=ctx, timeout=10) as r:
            wp_nonce = r.read().decode().strip()
        if not wp_nonce or len(wp_nonce) < 8:
            log(f"Yoast save: bad nonce '{wp_nonce}', falling back to edit", "WARN")
            return False
        # 构造 Yoast save POST
        post = xmlrpc_call("wp.getPost", (1, WP_USERNAME, WP_APP_PASSWORD, post_id))
        post_content = post.get("post_content", "")
        post_excerpt = post.get("post_excerpt", "")
        # 获取当前 post 的 Yoast custom_fields
        focus_kw = ""
        for cf in post.get("custom_fields", []):
            if cf.get("key") == "_yoast_wpseo_focuskw":
                focus_kw = cf.get("value", "")
                break
        data = json.dumps({
            "action": "wpseo_save_editor",
            "wpseo_front_id": "",
            "wpseo_focuskw": focus_kw,
            "wpseo_title": "",
            "wpseo_metadesc": "",
            "wpseo_canonical": "",
            "wpseo_redirect": "",
            "wpseo_opengraph-title": "",
            "wpseo_opengraph-desc": "",
            "wpseo_twitter-title": "",
            "wpseo_twitter-desc": "",
            "wpseo_focuskw_text_input": focus_kw,
            "post_id": post_id,
            "content": post_content,
            "excerpt": post_excerpt,
        }).encode()
        ajax_req = urllib.request.Request(
            f"{WP_SITE_URL}/wp-admin/admin-ajax.php",
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-WP-Nonce": wp_nonce,
                "User-Agent": "Mozilla/5.0",
            }, method="POST"
        )
        with urllib.request.urlopen(ajax_req, context=ctx, timeout=15) as r2:
            resp = r2.read().decode(errors="ignore")
        log(f"Yoast save triggered (resp={resp[:100]})", "OK")
        return True
    except Exception as e:
        log(f"Yoast save trigger failed: {e}", "WARN")
        return False

# ============================================================
# FAQ SCHEMA INJECTION (Google FAQ 富摘要)
# ============================================================
def _extract_faq_pairs(html_content: str) -> list:
    """
    从文章 HTML 中提取 FAQ Q&A 对。
    匹配模式：
      - H2/H3 标题含 "?"（独立问题）
      - H2/H3 含 "faq" / "frequently asked"
      - H3 紧跟 dl/dt/dd 定义列表
    返回: [{"question": str, "answer": str}, ...]
    """
    import re
    pairs = []
    # 清理HTML实体
    clean = lambda s: re.sub(r'<[^>]+>', '', s).strip()

    # 方式1: 标题含 ? 的 H2/H3，后面紧跟的段落是答案
    heading_pattern = re.compile(
        r'<h([23])[^>]*>(.*?)</h\1>\s*<p[^>]*>(.*?)</p>',
        re.DOTALL | re.IGNORECASE
    )
    for m in heading_pattern.finditer(html_content):
        heading_level = int(m.group(1))
        heading_text = clean(m.group(2))
        body_text = clean(m.group(3))
        # 问题必须有内容；答案至少要有实质内容
        if (('?' in heading_text or heading_level == 3) and
                len(heading_text) > 8 and
                len(body_text) > 30):
            pairs.append({"question": heading_text, "answer": body_text})

    # 方式2: dl/dt/dd 结构（FAQ定义列表）
    dl_pattern = re.compile(
        r'<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>',
        re.DOTALL | re.IGNORECASE
    )
    for m in dl_pattern.finditer(html_content):
        q = clean(m.group(1))
        a = clean(m.group(2))
        if len(q) > 5 and len(a) > 20:
            pairs.append({"question": q, "answer": a})

    # 去重（按问题文本）
    seen = set()
    unique = []
    for p in pairs:
        key = p["question"].lower()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique


def _inject_faq_schema(post_id: int, html_content: str) -> bool:
    """
    检测文章中的 FAQ 内容，构建 FAQPage JSON-LD，
    通过 REST API 注入到文章底部（追加到 post_content）。
    返回 True / False（不阻断发布，只是增强 SEO）。
    """
    try:
        faq_pairs = _extract_faq_pairs(html_content)
        if len(faq_pairs) < 2:
            log(f"FAQ schema: 仅找到 {len(faq_pairs)} 个Q&A，跳过（需≥2）", "INFO")
            return False

        schema = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": p["question"],
                 "acceptedAnswer": {
                     "@type": "Answer",
                     "text": p["answer"]
                 }}
                for p in faq_pairs
            ]
        }
        schema_json = json.dumps(schema, ensure_ascii=False)
        # 转义 </script> 防止浏览器提前闭合
        schema_json = schema_json.replace("</script>", "<\\/script>")

        ld_block = (
            f'\n\n'
            f'<!-- FAQ Schema injected by auto-publisher -->\n'
            f'<script type="application/ld+json">{schema_json}</script>\n'
        )

        # 获取当前 post_content
        post = xmlrpc_call("wp.getPost", (1, WP_USERNAME, WP_APP_PASSWORD, post_id))
        current_content = post.get("post_content", "")

        # 避免重复注入
        if '"@type": "FAQPage"' in current_content:
            log("FAQ schema 已存在，跳过", "INFO")
            return True

        updated_content = current_content + ld_block

        # 通过 wp.editPost 更新内容（不带封面图参数，避免覆盖）
        result = xmlrpc_call("wp.editPost", (
            post_id,
            WP_USERNAME, WP_APP_PASSWORD,
            {"post_content": updated_content}
        ))
        if result:
            log(f"FAQ schema 注入成功: {len(faq_pairs)} Q&A对 → PostID={post_id}", "OK")
            return True
        else:
            log(f"FAQ schema wp.editPost 返回 False（可能内容无变化）", "WARN")
            return True  # 非致命，视为成功

    except Exception as e:
        log(f"FAQ schema 注入失败: {e}", "WARN")
        return False

# ============================================================
# PUBLISH POST
# ============================================================
def publish_post(topic, content, img_url="", att_id=None):
    """Publish article to WordPress.
    img_url: WP media library URL (wp-content/uploads/...)
    att_id: attachment ID from wp.uploadFile. If provided directly, saves a lookup.
    The thumbnail is set via post_thumbnail in wp.newPost struct (most reliable).
    Falls back to wp.editPost 5-param if att_id not provided at call time.
    """
    cat_id = topic.get("category_id", 7)
    raw_kw = topic.get("keywords", [])
    tag_names = [k.strip() for k in raw_kw[:5] if k.strip()]
    # 把 tag 名称转为已有 term_id（避免传字符串触发 403 bug）
    tag_ids = _resolve_tag_ids(tag_names)
    int_links = topic.get("int_links", [])
    # 插入 Related Articles 内部链接区块
    if int_links:
        related_block = '\n\n' + '<h2>Related Articles</h2>\n<ul>\n'
        for link_title, link_url in int_links[:3]:
            related_block += f'<li><a href="{link_url}" target="_blank">{link_title}</a></li>\n'
        related_block += '</ul>\n'
        h2_positions = [m.start() for m in re.finditer(r'<h2>', content)]
        if len(h2_positions) >= 2:
            content = content[:h2_positions[-1]] + related_block + content[h2_positions[-1]:]
        else:
            content += '\n\n' + related_block
    # 自动生成 meta_desc
    meta_desc = topic.get("meta_desc", "").strip()
    if not meta_desc:
        kw = topic.get("focus_kw", "")
        meta_desc = f"Discover the best {kw} in 2026. This comprehensive guide compares top tools, features, pricing, and pros/cons to help you choose the right solution."
        if len(meta_desc) > 155:
            meta_desc = meta_desc[:152] + "..."
    # ✅ 正确时间格式：WP XML-RPC 接受 T 分隔格式 "YYYYMMDDTHH:MM:SS"
    now_gmt = datetime.now(timezone.utc).strftime("%Y%m%dT%H:%M:%S")
    custom_fields = [
        {"key": "_yoast_wpseo_focuskw", "value": topic["focus_kw"]},
        {"key": "_yoast_wpseo_title", "value": topic.get("seo_title", f"{topic['title']} - BestAItoolsite")},
        {"key": "_yoast_wpseo_metadesc", "value": meta_desc},
    ]
    # ✅ 正确方式：terms + 整数ID（Hostinger WP 不支持 tax_input，会500崩溃）
    # 分类必须用已有分类ID（查 CATEGORY_MAP）；tag 用 _resolve_tag_ids() 转为已有tag ID
    #
    # ✅ 封面图方案（两种，任选其一）：
    #   方案A（推荐，新文章）：post_thumbnail 直接写在 wp.newPost struct 里
    #   方案B（已知attachment_id）：发布后用 wp.editPost 5-param 设
    # ⚠️ 注意：wp.editPost 的 4-param 方式 (post_id, struct) 会静默失败
    post_data = {
        "post_title": topic["title"],
        "post_content": content,
        "post_excerpt": meta_desc,
        "post_status": "publish",
        "post_date": now_gmt,
        "post_date_gmt": now_gmt,
        "terms": {"category": [cat_id], "post_tag": tag_ids},
        "custom_fields": custom_fields,
    }
    # 方案A：如果已有 att_id，直接写入 struct
    if att_id:
        post_data["post_thumbnail"] = att_id
        log(f"Thumbnail will be set via post_thumbnail in newPost (att_id={att_id})", "OK")
    post_id = xmlrpc_call("wp.newPost", (1, WP_USERNAME, WP_APP_PASSWORD, post_data))
    log(f"Article published PostID={post_id} (cat={cat_id}, tags={tag_ids})", "OK")

    # ============================================================
    # 修复 wp.newPost 创建 status=future 的问题：立即检测并用 REST API 回填过去日期
    # WordPress 的 wp.newPost 有时会自动把 post_date 设为未来时间（时区/服务器时间问题）
    # 这导致文章前台返回 404（排期文章不显示），必须立即修复
    # ============================================================
    try:
        past_date = (datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        resp_fix = requests.post(
            f"{WP_SITE_URL}/wp-json/wp/v2/posts/{post_id}",
            json={"date": past_date, "status": "publish"},
            headers={"Content-Type": "application/json"},
            auth=(WP_USERNAME, WP_APP_PASSWORD),
            timeout=15
        )
        if resp_fix.status_code in (200, 201):
            log(f"Status/date fix applied: publish date set to past", "OK")
        else:
            log(f"Status/date fix failed: HTTP {resp_fix.status_code} — {resp_fix.text[:200]}", "WARN")
    except Exception as e:
        log(f"Status/date fix error: {e}", "WARN")

    # 触发 Yoast SEO save_post 分析
    time.sleep(2)
    _trigger_yoast_save(post_id)
    # 方案B：如果发布时没传 att_id，但有 img_url，走 wp.editPost 5-param兜底
    if not att_id and img_url and 'wp-content/uploads' in img_url:
        try:
            _att_id = find_wp_attachment_id(img_url)
            if _att_id:
                time.sleep(2)
                result = xmlrpc_call("wp.editPost", (1, WP_USERNAME, WP_APP_PASSWORD, post_id, {
                    "post_type": "post", "post_thumbnail": _att_id,
                }))
                if result:
                    log(f"Featured image set via editPost 5-param (att_id={_att_id})", "OK")
                else:
                    log(f"Featured image editPost returned False (retrying)...", "WARN")
                    time.sleep(3)
                    result2 = xmlrpc_call("wp.editPost", (1, WP_USERNAME, WP_APP_PASSWORD, post_id, {
                        "post_type": "post", "post_thumbnail": _att_id,
                    }))
                    log(f"Featured image retry: {'OK' if result2 else 'STILL FAILED'}", "WARN" if not result2 else "OK")
        except Exception as e:
            log(f"Featured image assign failed: {e}", "WARN")
    return post_id

# ============================================================
# MAIN DISPATCHER
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--daily-one", action="store_true")
    parser.add_argument("--single", type=int, default=None, help="Publish a specific article by ID")
    args = parser.parse_args()

    # --single: publish one specific article by ID
    if args.single is not None:
        topics = load_topics()
        topic = next((t for t in topics if t["id"] == args.single), None)
        if not topic:
            log(f"Article ID={args.single} not found in ARTICLES", "ERROR")
            return
        log(f"Single publish: [{topic['category']}] {topic['title']}")
        content = generate_article_content(topic)
        html_int_raw = re.findall(_INT_LINK_RE, content)
        log(f"  After generate: {len(html_int_raw)} internal links, {len(content)} chars")
        content = clean_article_content(content, topic.get("title", ""))
        html_int_clean = re.findall(_INT_LINK_RE, content)
        log(f"  After clean: {len(html_int_clean)} internal links")
        content = fix_long_paragraphs(content)
        html_int_fix = re.findall(_INT_LINK_RE, content)
        log(f"  After fix: {len(html_int_fix)} internal links")
        if len(html_int_raw) != len(html_int_fix):
            log(f"  WARNING: internal links changed {len(html_int_raw)} -> {len(html_int_fix)}", "WARN")

        # 如果缺少内部链接，自动重试生成（最多2次）
        retry_count = 0
        while retry_count < 2 and len(html_int_fix) < 1:
            retry_count += 1
            log(f"  Internal links missing (attempt {retry_count}/2), regenerating...", "WARN")
            content = generate_article_content(topic)
            html_int_raw = re.findall(_INT_LINK_RE, content)
            content = clean_article_content(content, topic.get("title", ""))
            content = fix_long_paragraphs(content)
            html_int_fix = re.findall(_INT_LINK_RE, content)
            log(f"  Retry {retry_count}: {len(html_int_fix)} internal links, {len(content)} chars")
        if len(html_int_fix) < 1:
            log("  No internal links after 2 retries (publish_post will inject Related Articles block)", "WARN")

        passed, issues = self_check_article(content, topic.get("title", ""), topic.get("focus_kw", ""))
        if not passed:
            log(f"Self-check issues: {' | '.join(issues[:3])}", "WARN")
            log("Publishing anyway (self-check is advisory only)", "WARN")
        # Extract first article image as fallback for featured image
        first_img_url = None
        m = re.search(r'<img\s+[^>]*src="([^">]+)"', content, re.IGNORECASE)
        if m:
            first_img_url = m.group(1)
        img_url, local_path = generate_featured_image(topic, fallback_img_url=first_img_url)
        if img_url and 'wp-content/uploads' in img_url:
            log(f"Featured image: {img_url[:80]}", "OK")
        if img_url:
            content = (f'<figure style="margin:0 0 30px;text-align:center;">'
                       f'<img src="{img_url}" alt="{topic["title"]}" '
                       f'style="width:100%;max-width:800px;border-radius:8px;" loading="lazy">'
                       f'</figure>') + content
        post_id = publish_post(topic, content, img_url)
        post_publish_validation(post_id, topic.get("category_id"))
        return

    if args.dry_run:
        log("Dry-run mode: testing generation")
        topics = load_topics()
        topic = select_balanced_topic(topics)
        if not topic:
            log("No topics available", "WARN")
            return
        log(f"Selected: [{topic['category']}] {topic['title']}")
        content = generate_article_content(topic)
        content = clean_article_content(content, topic.get("title", ""))
        content = fix_long_paragraphs(content)
        passed, issues = self_check_article(content, topic.get("title", ""), topic.get("focus_kw", ""))
        print(f"\n--- Content preview (first 800 chars) ---\n")
        print(content[:800])
        print(f"\n--- Self-check: {'PASS' if passed else 'FAIL'} ---")
        for iss in issues:
            print(f"  - {iss}")
        return

    if args.daily_one:
        log("Daily-one mode: selecting balanced article")
        topics = load_topics()
        topic = select_balanced_topic(topics)
        if not topic:
            log("All topics published or WP query failed", "WARN")
            return
        log(f"Selected: [{topic['category']}] {topic['title']}")
        content = generate_article_content(topic)
        html_int_raw = re.findall(_INT_LINK_RE, content)
        log(f"  After generate: {len(html_int_raw)} internal links, {len(content)} chars")
        content = clean_article_content(content, topic.get("title", ""))
        html_int_clean = re.findall(_INT_LINK_RE, content)
        log(f"  After clean: {len(html_int_clean)} internal links")
        content = fix_long_paragraphs(content)
        html_int_fix = re.findall(_INT_LINK_RE, content)
        log(f"  After fix: {len(html_int_fix)} internal links")
        if len(html_int_raw) != len(html_int_fix):
            log(f"  WARNING: internal links changed {len(html_int_raw)} -> {len(html_int_fix)}", "WARN")

        # 如果缺少内部链接，自动重试生成（最多2次）
        retry_count = 0
        while retry_count < 2 and len(html_int_fix) < 1:
            retry_count += 1
            log(f"  Internal links missing (attempt {retry_count}/2), regenerating...", "WARN")
            content = generate_article_content(topic)
            html_int_raw = re.findall(_INT_LINK_RE, content)
            content = clean_article_content(content, topic.get("title", ""))
            content = fix_long_paragraphs(content)
            html_int_fix = re.findall(_INT_LINK_RE, content)
            log(f"  Retry {retry_count}: {len(html_int_fix)} internal links, {len(content)} chars")
        if len(html_int_fix) < 1:
            log("  No internal links after 2 retries (publish_post will inject Related Articles block)", "WARN")

        passed, issues = self_check_article(content, topic.get("title", ""), topic.get("focus_kw", ""))
        if not passed:
            log(f"Self-check issues: {' | '.join(issues[:3])}", "WARN")
            log("Publishing anyway (self-check is advisory only)", "WARN")
        # Extract first article image as fallback for featured image
        first_img_url = None
        m = re.search(r'<img\s+[^>]*src="([^">]+)"', content, re.IGNORECASE)
        if m:
            first_img_url = m.group(1)
        img_url, local_path = generate_featured_image(topic, fallback_img_url=first_img_url)
        if img_url and 'wp-content/uploads' in img_url:
            log(f"Featured image: {img_url[:80]}", "OK")
        if img_url:
            content = (f'<figure style="margin:0 0 30px;text-align:center;">'
                       f'<img src="{img_url}" alt="{topic["title"]}" '
                       f'style="width:100%;max-width:800px;border-radius:8px;" loading="lazy">'
                       f'</figure>') + content
        post_id = publish_post(topic, content, img_url)
        post_publish_validation(post_id, topic.get("category_id"))
        return

    if args.execute:
        post_publish_validation(post_id, topic.get("category_id"))
        return

    if args.execute:
        log("Execute mode: publishing all articles")
        topics = load_topics()
        results = []
        for i, topic in enumerate(topics):
            if topic["id"] in PUBLISHED_ARTICLE_IDS:
                log(f"[{i+1}/{len(topics)}] SKIP {topic['title'][:50]} (already published)")
                continue
            log(f"[{i+1}/{len(topics)}] {topic['title'][:50]}")
            content = generate_article_content(topic)
            content = clean_article_content(content, topic.get("title", ""))
            content = fix_long_paragraphs(content)
            passed, issues = self_check_article(content, topic.get("title", ""), topic.get("focus_kw", ""))
            if not passed:
                log(f"Self-check FAILED: {' | '.join(issues)}", "ERROR")
                results.append((topic["id"], topic["title"][:40], False, "quality_fail"))
                continue
            img_url, local_path = generate_featured_image(topic)
            if not img_url:
                first_img_match = re.search(r'<img\s+[^>]*src="([^">]+)"', content, re.IGNORECASE)
                if first_img_match:
                    img_url = first_img_match.group(1)
                    log(f"Using first article image as featured: {img_url[:80]}", "OK")
                    if img_url and 'wp-content/uploads' not in img_url:
                        wp_uploaded_url, _ = upload_image_to_wp(img_url, topic)
                        if wp_uploaded_url:
                            img_url = wp_uploaded_url
                            log(f"Uploaded external image to WP media: {img_url[:80]}", "OK")
            if img_url:
                content = (f'<figure style="margin:0 0 30px;text-align:center;">'
                           f'<img src="{img_url}" alt="{topic["title"]}" '
                           f'style="width:100%;max-width:800px;border-radius:8px;" loading="lazy">'
                           f'</figure>') + content
            try:
                post_id = publish_post(topic, content, img_url)
                results.append((topic["id"], topic["title"][:40], True, f"post_id={post_id}"))
            except Exception as e:
                results.append((topic["id"], topic["title"][:40], False, str(e)))
            time.sleep(5)
        log(f"\n{'='*50}")
        ok_count = sum(1 for r in results if r[2])
        log(f"Results: published={ok_count} failed={len(results)-ok_count}")
        for rid, rtitle, rok, rmsg in results:
            log(f"  {'OK' if rok else 'FAIL'} ID={rid} {rtitle} {rmsg}")
        return

    log("Usage: xmlrpc_publisher.py --dry-run | --execute | --daily-one | --single ID", "INFO")

if __name__ == "__main__":
    main()
