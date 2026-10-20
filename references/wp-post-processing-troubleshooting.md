# WP 后处理操作排障记录
> 生成时间: 2026-05-29 | 适用版本: regenerate_and_publish.py ~1778行

---

## 问题3：`wp.newPost` XML-RPC 创建文章后 content 丢失（2026-05-31 新增）

### 症状
日志显示"文章新建成功: PostID=XXX"，但 WordPress REST API 查询该 PostID 返回 `status: None`、`content len: 0`，slug 也不正确。

### 根因
`xmlrpc_client.wp.newPost` 在 {{WP_SITE_DOMAIN}} 上传 `post_content` 字段后，`wp.getPost` 查询该 post_id 返回 `post_content` 为空字符串。同样的内容用 REST API `POST /wp-json/wp/v2/posts` 可以正常写入并读取。

### 修复方案（v4.7）
改用 REST API 创建文章（`urllib.request` + base64 认证），完全替换 `xmlrpc_call("wp.newPost", ...)` 调用。

```python
import base64, ssl, urllib.request, json
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
    f"{WP_SITE_URL}/wp-json/wp/v2/posts",
    data=payload,
    headers=hdrs,
    method="POST",
)
with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
    result = json.loads(resp.read())
    post_id = result["id"]
```

### 验证方法
```bash
curl -s -u "user:app_password" "https://{{WP_SITE_DOMAIN}}/wp-json/wp/v2/posts/{post_id}"
# 正常返回: "status": "publish", "content": {"rendered": "<p>..."}

### 症状
自动发布脚本日志：
```
[WARN] 标签搜索/创建失败 'AI coding agent 2026': string indices must be integers, not 'str'
```

### 根因
`wp.getTerms` 返回的响应不是预期的 dict/list 结构，而是字符串（可能是 XML-RPC 错误响应被当作数据）。代码直接对 `terms` 取下标 `t["name"]` 导致报错。

### 修复
在 `_ensure_tag_id()` 中增加类型检查：
```python
if isinstance(terms, list):
    for t in terms:
        if isinstance(t, dict) and t.get("name", "").lower() == tag_name.lower():
            return int(t["term_id"])
```

同时处理 `wp.newTerm` 500 "already exists" 异常兜底：
```python
except xmlrpc.client.Fault as e:
    if e.faultCode == 500 and "already exists" in e.faultString:
        # 已存在但搜索失败，重新搜索
        terms2 = xmlrpc_call("wp.getTerms", ...)
```

位置：`regenerate_and_publish.py` lines 1688-1711（2026-05-29）

## 问题1：标签设不上（HTTP 400 / REST 401）

### 症状
自动发布脚本日志：
```
[WARN] 标签设失败: HTTP Error 400: Bad Request
```
或：
```
[ERROR] 401 Unauthorized  (execute_code 环境)
```

### 根因
**`{{WP_USERNAME}}` 账户对 REST API 的写操作权限不足。**

- `POST /wp-json/wp/v2/posts/{id}` + `{"tags":[...]}` → 返回 `400` 或 `[]` 空响应
- `POST /wp-json/wp/v2/tags` → 返回 `401 Unauthorized` (execute_code 沙盒)
- WP 服务器通过代理时，REST 写操作全部被拒；`curl` 直接调则不稳定（503）

### 排查方法
```bash
# 用 curl 直接测试（不走代理，绕过 ACL 问题）
AUTH="{{WP_USERNAME}}:{{WP_APP_PASSWORD}}"
WP_URL="https://{{WP_SITE_DOMAIN}}"

# 1. 测试 GET 是否正常
curl -s -u "$AUTH" "$WP_URL/wp-json/wp/v2/posts/1114?_fields=tags"
# 期望: {"tags":[]} 或实际 tag ID 列表

# 2. 测试 POST 设标签是否正常
curl -s -u "$AUTH" -X POST -H "Content-Type: application/json" \
  -d '{"tags":[222,223]}' \
  "$WP_URL/wp-json/wp/v2/posts/1114" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tags',[]))"
# 期望: 实际 tag ID 列表。若返回 [] 或 401，则走 XML-RPC 备用方案
```

### 正确修复方案（已验证）
用 `wp.editPost` via XML-RPC，权限充足：
```python
import xmlrpc.client, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
s = xmlrpc.client.ServerProxy(f"{WP_SITE_URL}/xmlrpc.php", context=ctx)

# 设标签（terms:post_tag 接收 tag ID 列表）
r = s.wp.editPost(1, WP_USERNAME, WP_APP_PASSWORD, post_id,
    {"terms": {"post_tag": [222, 223, 224]}})

# 设 Yoast custom_fields
yoast_fields = {
    "_yoast_wpseo_metadesc": "Description here...",
    "_yoast_wpseo_title": "SEO Title Here"
}
r = s.wp.editPost(1, WP_USERNAME, WP_APP_PASSWORD, post_id,
    {"custom_fields": [{"key": k, "value": v} for k, v in yoast_fields.items()]})
```

### 不生效的方法（已排除）
- `wp.setPostMetadata` — 方法不存在（Jetpack RPC）
- `wp.setPostTerms` — 方法不存在（WordPress 未实现）
- REST API `POST /posts/{id}` + `{"tags":[...]}` — `{{WP_USERNAME}}` 权限不足

---

## 问题2：Yoast SEO 触发失败

### 症状
```
[WARN] Yoast save trigger failed: HTTP Error 400: Bad Request
```

### 根因
`_trigger_yoast_save()` 依赖 WP Admin AJAX 端点（`/wp-admin/admin-ajax.php`），该端点需要：
1. 有效的 WP nonce（从 `/wp-admin/admin-ajax.php?action=rest-nonce` 获取）
2. 有效的 WP admin session cookie

自动脚本在无头环境（无浏览器 cookie）下，nonce 获取成功但后续 AJAX 请求仍需认证，导致 `400 Bad Request`。

### 正确修复方案（已验证）
用 `wp.editPost` + `custom_fields` 设 Yoast 元数据：
```python
# 设 focus keyword
r = s.wp.editPost(1, WP_USERNAME, WP_APP_PASSWORD, post_id,
    {"custom_fields": [
        {"key": "_yoast_wpseo_focuskw", "value": "Fireflies AI meeting notes"},
        {"key": "_yoast_wpseo_metadesc", "value": "I tested Fireflies.ai..."},
        {"key": "_yoast_wpseo_title", "value": "Fireflies AI Review 2026..."},
    ]})
```
无需 nonce，无需 cookie，XML-RPC 直接写入数据库。

---

## 问题3：WP 服务器网络不稳定

### 症状
```
[WARN] REST GET 失败#1: IncompleteRead(786432 bytes read)
[WARN] REST GET 失败#1: <urlopen error Tunnel connection failed: 503 Service Unavailable>
[WARN] WP媒体上传失败: IncompleteRead(11545 bytes read)
[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol
[REMOTE] Remote end closed connection without response
```

### 根因
LiteSpeed Cache 代理层在处理长连接（>10KB 响应）时会中断连接，导致：
- 长内容读取（`wp.getPost` / REST `_fields=content`）全部超时
- 媒体上传（封面图）间歇性失败
- 轻量 GET（用户数、分类列表）基本稳定

### 应对策略
1. **重试机制**：所有 HTTP 请求使用指数退避重试（`delay * (attempt+1)`）
2. **超时控制**：设 `timeout=40`，避免永久挂起
3. **长内容绕过**：文章内容通过 `wp.newPost` 直接写入，不依赖后续 `getPost` 读取
4. **图片兜底**：上传失败时保留本地文件，不阻塞发布流程

---

## 问题4：`int_links` 空列表导致 IndexError

### 症状
```
IndexError: list index out of range  (line 226: int_link1_title = article["int_links"][0][0])
```

### 根因
`build_prompt()` 函数直接访问 `article["int_links"][0][0]` 和 `[1][0]`，但已发布全部 27 篇文章的 `int_links` 字段均为空列表 `[]`。

### 修复
```python
all_int_links = article.get("int_links") or []
int_link1_title = all_int_links[0][0] if len(all_int_links) > 0 else article["tools"][0]
int_link2_title = all_int_links[1][0] if len(all_int_links) > 1 else article["tools"][1] if len(article["tools"]) > 1 else article["tools"][0]
```

---

## 快速命令备忘

### 手动补救单篇文章的标签 + Yoast
```bash
AUTH="{{WP_USERNAME}}:{{WP_APP_PASSWORD}}"
WP_URL="https://{{WP_SITE_DOMAIN}}"
POST_ID=1114  # 改成实际 PostID

# 1. 搜索/创建 tag
for tag in "Tag1" "Tag2"; do
  result=$(curl -s -u "$AUTH" "$WP_URL/wp-json/wp/v2/tags?search=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$tag'))")&per_page=5")
  existing=$(echo "$result" | python3 -c "import sys,json; tags=json.load(sys.stdin); print([t['id'] for t in tags if t.get('name','').lower()=='$tag'.lower()])")
  if [ "$existing" != "[]" ]; then
    echo "已有: '$tag' → $(echo "$existing" | python3 -c 'import sys,json; print(json.load(sys.stdin)[0])')"
  else
    create=$(curl -s -u "$AUTH" -X POST -H "Content-Type: application/json" -d "{\"name\":\"$tag\"}" "$WP_URL/wp-json/wp/v2/tags")
    echo "新建: '$tag' → $(echo "$create" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')"
  fi
  sleep 1
done

# 2. 用 XML-RPC 设标签 + Yoast
cd {{WORKDIR}} && python3 << 'PYEOF'
import xmlrpc.client, ssl, sys; sys.path.insert(0, '.')
from shared_config import WP_SITE_URL, WP_USERNAME, WP_APP_PASSWORD
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
s = xmlrpc.client.ServerProxy(f"{WP_SITE_URL}/xmlrpc.php", context=ctx)
# 设标签
r = s.wp.editPost(1, WP_USERNAME, WP_APP_PASSWORD, 1114,
    {"terms": {"post_tag": [222, 223, 224, 225, 226]}})
print("标签:", r)
# 设 Yoast
r = s.wp.editPost(1, WP_USERNAME, WP_APP_PASSWORD, 1114,
    {"custom_fields": [
        {"key": "_yoast_wpseo_metadesc", "value": "Description here..."},
        {"key": "_yoast_wpseo_title", "value": "SEO Title Here"},
    ]})
print("Yoast:", r)
PYEOF
```

### 验证文章最终状态
```bash
curl -s -u "$AUTH" "$WP_URL/wp-json/wp/v2/posts/POST_ID?_fields=tags,meta" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); meta=d.get('meta',{}); print('tags:', d.get('tags',[])); print('Yoast:', meta.get('_yoast_wpseo_metadesc','❌')[:80])"
```

---

## 代码修复记录

| 修复项 | 文件 | 行号 | 日期 |
|--------|------|------|------|
| `int_links` 空列表容错 | `regenerate_and_publish.py` | 223-228 | 2026-05-29 |
| `_set_wp_post_tags` 改用 `wp.editPost` XML-RPC | `regenerate_and_publish.py` | ~1467-1527 | 待更新 |
| Yoast save 改用 `wp.editPost` + `custom_fields` | `regenerate_and_publish.py` | ~1397-1401 | 待更新 |