# AutoWPContentPublishWorkflow

> **AI 驱动的 WordPress 内容发布流水线** — 从选题到 SEO 优化的全自动化工作流
> Built from 4 months of production use, 80+ published articles, and 27 hard-won bug fixes.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![WordPress](https://img.shields.io/badge/WordPress-6.0+-21759B.svg)](https://wordpress.org/)
[![Maintenance](https://img.shields.io/badge/maintained-yes-green.svg)](https://github.com/zhoudibo/autoWPcontentpublishworkflow/graphs/commit-activity)

---

## 为什么做这个？

2026年8月，我的WordPress站点遇到Google收录危机：**82篇文章，GSC只收录4页**。

排查后发现，问题不在服务器、不在robots.txt、不在sitemap——**问题在发布流水线的27个静默Bug**。每一个单独看都不致命，但叠加起来让整站SEO权重完全无法传递。

这个项目把那些调试过程沉淀成了可复用的流水线，也把踩过的坑全部记录在文档里。

**结果**：GSC 收录从 4 页 → 82 篇全覆盖（技术修复后）。

---

## 核心特性

| 特性 | 说明 |
|------|------|
| **REST API 优先** | 全流程走 `wp-json/wp/v2`，绕开 XML-RPC 的 `post_content` 丢失问题 |
| **叙事型 H2 强制门控** | 自动检测模板 H2（Introduction/Conclusion/FAQ...），超标触发重写 |
| **AI 腔清洗** | 37 个 AI 高频词启发式检测 + 段落均匀度 + 词汇重复率，0-100 分评分 |
| **动态内链注入** | 从已发布文章池自动发现相关文章并注入，修复权重传递断裂 |
| **外链 nofollow 自动** | 所有站外链接自动追加 `rel="nofollow noopener noreferrer"` |
| **重试机制** | `IncompleteRead` / `ConnectionReset` 指数退避重试（15s/30s/45s） |
| **封面图 AI 生成** | 调用图像生成 API 产出内容贴合的真实图片（非 PIL 纯色图） |
| **双绿标准** | SEO good + Readability good 双达标才允许发布 |
| **配置外置** | 所有凭据走环境变量，代码零硬编码密钥 |

---

## 快速开始

### 1. 环境要求

- Python 3.9+
- 一个 WordPress 站点
- 一个 AI API Key（支持 OpenAI 兼容接口，如 MiniMax）

### 2. 安装

```bash
git clone https://github.com/zhoudibo/autoWPcontentpublishworkflow.git
cd autoWPcontentpublishworkflow
pip install -r requirements.txt
```

### 3. 配置

```bash
cp config/.env.example config/.env
```

编辑 `config/.env`：

```bash
WP_SITE_DOMAIN=your-site.com
WP_USERNAME=your-wp-user
WP_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx-xxxx   # WP后台生成的 Application Password
MINIMAX_API_KEY=your-api-key
```

> **安全提示**：`.env` 已被 `.gitignore` 忽略，不会被提交到仓库。
> Application Password 获取方式：WP后台 → 用户 → 个人资料 → 勾选"应用程序密码"

验证配置：

```bash
python3 config/config.py
```

### 4. 准备选题

编辑 `topics/topics.json`，或在 `scripts/shared_config.py` 的 `ARTICLES` 中添加：

```json
{
  "id": 101,
  "title": "I Used [Tool] for 30 Days — Here's What Actually Happened",
  "slug": "tool-30-day-real-test-2026",
  "category_id": 6,
  "focus_kw": "tool 30 day test 2026",
  "keywords": ["tool", "review", "test"],
  "tools": ["Tool A", "Tool B"],
  "ext_links": [["Official Site", "https://example.com"]]
}
```

### 5. 发布

```bash
cd scripts

# 列出所有选题
python3 publish_topic.py --list

# 干跑测试（生成但不发布）
python3 publish_topic.py --dry --id=101

# 发布指定选题
python3 publish_topic.py --id=101

# 发布队列中下一篇
python3 publish_topic.py --next
```

### 6. 全站质量审计

```bash
python3 site_quality_audit.py              # 只审计
python3 site_quality_audit.py --fix        # 审计 + 自动修复
python3 site_quality_audit.py --fix --id 1234  # 只处理指定文章
```

---

## 项目结构

```
autoWPcontentpublishworkflow/
├── config/
│   ├── .env.example          # 配置模板（复制为 .env 后填入真实值）
│   └── config.py             # 统一配置加载器（环境变量 → 运行时对象）
├── scripts/
│   ├── shared_config.py      # 单一真实源：API Key/选题库/分类映射/清洗函数
│   ├── publish_topic.py      # 发布编排器（CLI 入口）
│   ├── regenerate_and_publish.py  # 内容生成 + 发布核心引擎
│   ├── xmlrpc_publisher.py   # XML-RPC 兼容层（降级方案）
│   └── site_quality_audit.py # 全站质量审计与批量修复
├── references/
│   ├── humanizer_patterns.md          # 29 种 AI 腔模式手册
│   └── wp-post-processing-troubleshooting.md  # WP 后处理故障排查
├── topics/
│   └── topics.json          # 选题库
├── SKILL.md                 # 技能文档（可导入 Claude Skills / Cursor Rules）
├── README.md                # 本文件
├── LICENSE                  # MIT
└── .gitignore
```

---

## 质量门控标准

发布一篇合格的文章，必须同时满足：

| 检查项 | 标准 | 级别 |
|--------|------|------|
| H2 模板占比 | < 40% | 🔴 阻断 |
| AI 腔评分 | < 70 分 | 🔴 阻断 |
| 词数 | ≥ 1500 | 🔴 阻断 |
| H2 占位符残留 | 0 个 | 🔴 阻断 |
| 外链 nofollow | 100% 覆盖 | 🟡 警告 |
| 内链数量 | ≥ 3 个 | 🟡 警告 |
| 封面图 | 已设置 | 🟡 警告 |

**为什么是这个标准？** Google 的 Helpful Content Update（HCU）会系统性降权模板化 AI 内容。这套门控直接对应 HCU 的识别特征：

- 模板 H2（Introduction/Conclusion/Pros and Cons）→ 典型机器写作结构
- 段落长度高度均匀 → 非人类写作特征
- AI 高频词密集 → LLM 生成的词汇指纹
- 词数不足 → 内容深度不匹配搜索意图

---

## 27 个 Bug 的完整记录

这个项目最有价值的部分是 `references/` 里那份故障排查手册。踩过的坑包括：

### 传输层

- **XML-RPC 静默丢失 post_content** — `wp.newPost` 返回成功 ID，但 content 实际为空，前台 404。必须改用 REST API。
- **PATCH 不带 status 字段导致文章进 trash** — WordPress 部分更新的隐式行为，极难排查。
- **IncompleteRead 瞬时错误** — Hostinger 长连接不稳定，需要指数退避。
- **macOS 代理下 urllib SSL EOF** — 同一请求 curl 正常，urllib 失败。改用 `http.client` 直连。

### 内容层

- **H2 正则批量替换摧毁 28 篇文章** — `re.DOTALL` + 嵌套量词导致灾难性回溯，正文被当成 H2 内容吞掉。改用 DOM 解析。
- **内链注入函数静默返回** — `if not int_links: return content` 导致 73 篇文章内链为 0。
- **字典键覆盖** — 相同标题的两篇文章互相覆盖 ID，force 更新打到错误的文章上。
- **字数组件覆盖** — WordPress 显示了 article A 的标题，内容却是 article B 的。

### SEO 层

- **HCU 整站降权** — 82 篇模板化内容被判定为低价值。
- **重复 slug** — 相同标题的文章，Google 每组只收录一篇。
- **外链只加 noopener 漏加 nofollow** — Google SEO 明确要求。
- **封面图纯色背景 + 文字** — 被判定为低质量，AIGC 元数据残留。

完整清单见 `references/wp-post-processing-troubleshooting.md`。

---

## AI 腔检测原理

不调用外部 API，纯本地启发式统计，0 成本：

| 维度 | 信号 | 权重 |
|------|------|------|
| AI 腔词汇命中 | 37 个高频 AI 词（"leverage" / "game-changing" / "in today's fast-paced world"） | 35 分 |
| 词汇重复率 | 词汇独特率 < 0.58 判定为 AI 重复 | 35 分 |
| 句子均匀度 | 句长变异系数 < 0.28 判定为过于均匀 | 15 分 |
| 段落均匀度 | 段落句子数变异 < 0.2 判定为模板化 | 10 分 |
| 句式复杂度 | 超长句和过度复杂结构识别 | 5 分 |

**实测数据**：本项目产出的 82 篇文章，AI 腔评分平均 **6.3%**（阈值 55% 为警告线）。

29 种完整模式见 `references/humanizer_patterns.md`，基于 Wikipedia 的 [Signs of AI Writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing) 标准整理。

---

## 定时自动发布

```bash
# 每 4 小时自动发布下一篇
0 */4 * * * cd /path/to/scripts && python3 publish_topic.py --next
```

配合系统 crontab 或任意调度器（systemd timer / launchd / GitHub Actions）即可实现无人值守发布。

---

## 为什么选 REST API 而不是 XML-RPC？

| 维度 | XML-RPC | REST API |
|------|---------|----------|
| 内容写入可靠性 | ❌ 部分主机上静默丢失 | ✅ 可靠 |
| 认证 | 明文密码传输 | Application Password |
| 响应可读性 | 需 XML 解析 | JSON |
| 媒体上传 | 需 multipart 手工拼 | 标准 multipart |
| 主机兼容性 | 很多主机已禁用 | 全支持 |

**结论**：除非有特殊需求，一律用 REST API。本项目保留 `xmlrpc_publisher.py` 仅作为降级方案。

---

## 内容真实性原则

**这条是硬性要求，不是建议。**

这个项目从真实项目中提炼选题，而非让模型凭空生成话题。原因很简单：Google 能识别出没有一手经验的 AI 内容，而读者也能。

正确做法：

1. 选题来自你真正做过的项目 / 真正用过的工具 / 真正踩过的坑
2. 技术细节必须可验证（真实的报错信息、真实的版本号、真实的性能数据）
3. 公司名、人名、金额做脱敏处理，但保留故事的真实结构
4. prompt 里带上"基于以下真实经历"的上下文，而不是让模型编

反例：

- ❌ "I saved $500/month with AI"（没有任何依据）
- ❌ "I tested 12 AI tools over 6 months"（根本没测过）

正例：

- ✅ "I Debugged an API That Was Silently Dropping Requests"（真实排查过程，技术细节可复现）
- ✅ "MyBatis Slow SQL Investigation: The Pattern All 11 Queries Shared"（真实的优化案例）

---

## 常见问题

<details>
<summary><b>发布时提示 <code>config/.env</code> 不存在</b></summary>

```bash
cp config/.env.example config/.env
# 然后编辑 config/.env 填入你的配置
python3 config/config.py  # 验证
```
</details>

<details>
<summary><b>REST API 返回 401 incorrect_password</b></summary>

你用的可能是 WordPress 登录密码。需要用 Application Password：

WP后台 → 用户 → 编辑你的用户 → 拉到最下面 → 勾选"应用程序密码" → 生成。
</details>

<details>
<summary><b>文章发布后前台 404</b></summary>

大概率是用 XML-RPC 发布的。改用 REST API（`publish_to_wp()` 已默认走 REST）。

另外检查 PATCH 请求是否携带了 `status` 字段，缺失会导致文章进回收站。
</details>

<details>
<summary><b>内链数量为 0</b></summary>

检查 `_inject_internal_links()` 是否被绕过。这个函数曾有一个经典 bug：`if not int_links: return content` 直接跳过整个注入逻辑。

本项目已修复：从 WP REST API 动态发现相关文章作为兜底。
</details>

<details>
<summary><b>macOS 代理环境下 SSL 错误</b></summary>

macOS 系统代理不会自动被 Python 读取。需要显式设置：

```bash
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
```

另外，`urllib` 在代理环境下容易出现 SSL EOF，建议直接用 `http.client` 或 `curl`。
</details>

---

## 贡献

欢迎提交 Issue 和 PR。如果你也踩过 WordPress 自动发布的坑，share 出来让更多人少走弯路。

---

## License

[MIT](LICENSE) © 2026 zhoudibo

---

## 相关技术

- [WordPress REST API Handbook](https://developer.wordpress.org/rest-api/)
- [Google Search Central - Helpful Content Update](https://developers.google.com/search/docs/fundamentals/creating-helpful-content)
- [Wikipedia: Signs of AI Writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing)
- [Yoast SEO](https://www.yoast.com/wordpress/plugins/seo/)
