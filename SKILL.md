---
name: autoWPContentPublishWorkflow
description: >
  AI 驱动的 WordPress 内容自动发布流水线。
  REST API + 质量门控 + 叙事型 H2 + AI 腔清洗 + 动态内链注入。
  包含 27 个生产环境 Bug 的完整排查记录。
category: automation
tags: [wordpress, rest-api, ai-content, seo, auto-publish, quality-gate, internal-links, humanizer]
license: MIT
version: 1.0
---

# AutoWPContentPublishWorkflow

WordPress 内容自动发布流水线。从选题 JSON 出发，经过 AI 内容生成、质量门控、
SEO 优化，最终发布到 WordPress。

**这个 skill 解决的核心问题**：自动化发布的流水线本身会静默损坏内容。
静默失败的自动化比手动更危险——因为它规模化地产生垃圾。

---

## 何时使用

- 需要批量/定时发布 WordPress 文章
- 需要保证 AI 生成内容通过 Google HCU（Helpful Content Update）
- 需要排查"发布成功但前台 404"或"内容丢失"类问题
- 需要自动化 SEO 处理（内链、nofollow、Yoast meta、封面图 ALT）

---

## 快速命令

```bash
cd scripts

# 配置检查（最先跑这个）
python3 ../config/config.py

# 列出选题
python3 publish_topic.py --list

# 干跑（生成内容但不发布，用于验证质量）
python3 publish_topic.py --dry --id=101

# 发布指定选题
python3 publish_topic.py --id=101

# 发布队列下一篇
python3 publish_topic.py --next

# 全站审计
python3 site_quality_audit.py
python3 site_quality_audit.py --fix
python3 site_quality_audit.py --fix --id 1234
```

---

## 流水线参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `MIN_WORDS` | 1500 | 低于此词数触发重写 |
| `TARGET_WORDS_MIN/MAX` | 1600 / 1800 | 目标词数区间 |
| `MAX_TOKENS_CONTENT` | 6144 | 主内容生成上限 |
| `MIN_INTERNAL_LINKS` | 3 | 最少内链数 |
| `RECOMMENDED_INTERNAL_LINKS` | 5 | 推荐内链数 |
| WP REST 重试 | 3 次（15s/30s/45s） | 应对 IncompleteRead |

---

## 质量门控（发布前必须全绿）

| 检查项 | 标准 | 违反后果 |
|--------|------|---------|
| H2 模板占比 | < 40% | 🔴 阻断发布，触发 H2 重写 |
| AI 腔评分 | < 70 分 | 🔴 阻断发布 |
| 词数 | ≥ 1500 | 🔴 阻断发布 |
| H2 占位符 | 0 个 | 🔴 阻断发布 |
| 外链 nofollow | 100% | 🟡 警告 |
| 内链数量 | ≥ 3 | 🟡 警告 |
| 封面图 | 已设置 | 🟡 警告 |

### 模板 H2 黑名单

以下 H2 是 Google HCU 的直接靶心，出现即超标：

```
Introduction / Conclusion / Summary / Overview / Key Features
Pros and Cons / What Is / How to Choose / Getting Started
The Best / Top Picks / Benefits / Drawbacks / FAQ
Review / Verdict / Features
```

### 叙事型 H2 正确形态

必须有**时间锚点 + 具体数字 + 情感判断 + 第一人称**：

```
✅ "The Day I Wasted 3 Hours on This Tool"
✅ "Week One: Why the Simple Solution Failed"
✅ "The $300 Mistake I Made in Month Two"
✅ "What Actually Worked in My 30-Day Test"

❌ "Key Features"
❌ "Pros and Cons"
❌ "Getting Started Guide"
```

---

## AI 腔检测算法

纯本地启发式，零 API 成本，加载时一次性计算：

| 维度 | 判定条件 | 权重 |
|------|----------|------|
| AI 高频词命中 | 37 个词的命中数 | 35 |
| 词汇重复率 | 独特率 < 0.58 | 35 |
| 句子均匀度 | 变异系数 < 0.28 | 15 |
| 段落均匀度 | 句子数变异 < 0.2 | 10 |
| 句式复杂度 | 超长句/过度复杂 | 5 |

**阈值**：≥70 阻断；55-70 警告；<55 通过。

完整模式清单见 `references/humanizer_patterns.md`（29 种）。

---

## 关键实现模式

### WP REST API 写操作统一用 curl 或 http.client

**不要用 `urllib.request`**——macOS 代理环境下会 SSL EOF：

```python
# ✅ 推荐：http.client 直连
import http.client, ssl
ctx = ssl.create_default_context()
conn = http.client.HTTPSConnection("your-site.com", context=ctx, timeout=60)
conn.request("GET", "/wp-json/wp/v2/posts?per_page=1", headers=hdrs)
resp = conn.getresponse()
data = resp.read()
conn.close()

# ✅ 或 curl subprocess
subprocess.run(["curl", "-s", "-u", auth, url], capture_output=True)
```

### PATCH 必须显式携带 status

```python
# ❌ 会导致文章进回收站
payload = {"content": new_content}

# ✅ 必须带 status
payload = {"content": new_content, "status": "publish"}
```

### 删除文章用 DELETE + force=true

```bash
curl -X DELETE "https://your-site.com/wp-json/wp/v2/posts/{id}?force=true" -u "user:pass"
# 错误方式：POST + {"status":"trash"} → 400 rest_invalid_param
```

### 内链注入必须动态发现

```python
# ❌ 经典 bug：预设列表为空就整个跳过
def inject(content, article):
    links = article.get("int_links", [])
    if links:
        ...  # 空列表时这段永远不执行
    return content

# ✅ 从 WP 动态获取相关文章作为兜底
def inject(content, article):
    related = fetch_published_posts_from_wp()  # 动态发现
    ...  # 保证每篇都有 Related Articles
```

### 批量 HTML 修改禁止 `re.DOTALL` + 嵌套量词

```python
# ❌ 灾难性回溯，会吞掉正文
pattern = re.compile(r'<h2([^>]*)>(.*?)</h2>', re.DOTALL)
inner = re.sub(r'([^<]*(?:<[^>]+>[^<]*)*)', cb, text)

# ✅ 用 DOM 解析
from bs4 import BeautifulSoup
soup = BeautifulSoup(html, 'html.parser')
for h2 in soup.find_all('h2'):
    if is_template(h2.text):
        h2.string = rewrite(h2.text)
html = str(soup)
```

**判断标准**：修改前后字数对比，修复后 < 原字数 30% 就是踩坑了。

---

## 27 个 Bug 分类速查

### 传输层
| 症状 | 根因 | 修复 |
|------|------|------|
| 发布成功但前台 404 | XML-RPC 静默丢 content | 改 REST API |
| 文章莫名进回收站 | PATCH 缺 status 字段 | 加 `"status": "publish"` |
| `IncompleteRead` 报错 | Hostinger 长连接不稳 | 指数退避重试 |
| SSL EOF / 503 | macOS 代理 + urllib | 用 http.client 直连 |

### 内容层
| 症状 | 根因 | 修复 |
|------|------|------|
| 28 篇文章正文消失 | DOTALL 嵌套量词 | 用 DOM 解析 |
| 全站内链为 0 | 函数静默 return | 动态发现兜底 |
| 标题和内容不匹配 | 字典键覆盖 | 硬编码 ID 映射 |
| 封面图是纯色+文字 | PIL 伪 AI 图 | 调真实图像生成 API |

### SEO 层
| 症状 | 根因 | 修复 |
|------|------|------|
| 全站不收录 | HCU 整站降权 | 质量门控 + 叙事 H2 |
| 同组只收录 1 篇 | 重复 slug | 删除重复 |
| 外链 SEO 扣分 | 缺 nofollow | 自动追加 |
| 图片 ALT 为空 | urllib PATCH 失败 | curl subprocess |

---

## 内容真实性（硬性要求）

**这条不是建议，是这个项目能 work 的根本原因。**

Google 能识别没有一手经验的 AI 内容，读者也能。

### 选题来源优先级

1. 你真正做过的项目
2. 你真正用过的工具（≥30 天）
3. 你真正踩过的坑
4. 你真正解决过的问题

### 脱敏规则

| 类型 | 处理方式 |
|------|---------|
| 公司名 | 替换为行业通用描述 |
| 人名 | 替换为角色（"a senior engineer"） |
| 具体金额 | 保留数量级，模糊具体数值 |
| 内网地址/IP | 完全删除 |
| 客户项目细节 | 抽象为通用场景 |

### 技术细节必须可验证

- 真实的报错信息（`IncompleteRead(0 bytes read)` 而不是 "some error"）
- 真实的版本号（`python-pptx 0.6.21` 而不是 "recent version"）
- 真实的性能数据（4.2s → 380ms 而不是 "much faster"）

---

## 内容策略与 HCU 避坑

| 维度 | ❌ 被降权 | ✅ 通过 |
|------|----------|--------|
| 文章类型 | 90% 工具对比 | 叙事型第一人称经历 |
| H2 结构 | Introduction/Pros/Cons | 叙事驱动 ≥60% |
| SEO Title | 依赖 Yoast 自动生成 | 每篇手写 ≤60 字符 |
| Meta Description | 69% 为空 | 每篇 120-155 字符 |
| 内容深度 | 600-900 词 | 1500+ 词 |
| 词数分布 | 每篇都一样 | 1200-2500 自然分布 |

### Meta Description 规范

```
格式：[具体动作] + [独特发现] + [读者收益]
长度：120-155 字符
示例："I rebuilt my entire publishing pipeline after 27 bugs. Here's what broke, what I fixed, and the three checks that now run before every post."
```

### SEO Title 规范

```
格式：[核心关键词] + [独特价值/数字] + | [站点名]
长度：≤ 60 字符
```

---

## 安全要求

1. **绝不硬编码凭据**——所有密钥走环境变量 / `.env`
2. **`.env` 必须进 `.gitignore`**
3. **用 Application Password**，不用管理员登录密码
4. **提交前扫描**：密码、API Key、域名、内网 IP、真实邮箱

```bash
# 提交前自检
grep -rEn "sk-[A-Za-z0-9]{30,}|password\s*=\s*['"][^'"]{8,}" --include="*.py" .
grep -rEn "/Users/[a-z]+|/home/[a-z]+" --include="*.py" .
```

---

## 目录结构

```
autoWPcontentpublishworkflow/
├── config/
│   ├── .env.example     # 配置模板
│   └── config.py        # 配置加载器
├── scripts/
│   ├── shared_config.py           # 单一真实源
│   ├── publish_topic.py           # 发布编排
│   ├── regenerate_and_publish.py  # 核心引擎
│   ├── xmlrpc_publisher.py        # XML-RPC 降级层
│   └── site_quality_audit.py      # 质量审计
├── references/
│   ├── humanizer_patterns.md
│   └── wp-post-processing-troubleshooting.md
├── topics/topics.json
├── README.md
└── LICENSE
```

**关键原则**：`fix_long_paragraphs()` 只存在于 `shared_config.py`。
其他文件必须 `from shared_config import fix_long_paragraphs`，禁止复制函数体。

---

## 参考文档

- `references/humanizer_patterns.md` — 29 种 AI 腔模式手册
- `references/wp-post-processing-troubleshooting.md` — WP 后处理故障排查（含 27 个 Bug 完整记录）

---

## License

MIT © 2026 zhoudibo
