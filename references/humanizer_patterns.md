# Humanizer Patterns — AI腔检测与修复参考手册
# 来源：blader/humanizer (MIT)，基于 Wikipedia Signs of AI Writing
# 版本：v1.0 | 日期：2026-08-06

---

## 核心原则

LLM 用统计模型猜测下一个最可能的词，结果趋向于「最安全/最通用的完成方式」，
这正是AI腔产生的根源。去AI味的本质是引入「不完美但真实」的人类表达特征。

---

## 29种AI腔模式

### A. 内容虚假升华类

#### 1. 过度强调重要性/遗产/趋势
**词汇**: stands as, serves as, testament to, reminder, vital role, significant,
pivotal, crucial, key role, underscores, highlights importance, reflects broader,
symbolizing, setting the stage for, marking/shaping, represents/marks a shift,
key turning point, evolving landscape, focal point, indelible mark, deeply rooted

**修复**: 删除升华句，直接陈述事实

#### 2. 过度强调知名度/媒体报道
**词汇**: independent coverage, leading expert, active social media presence,
widely regarded, has gained recognition

**修复**: 替换为具体引用或删除

#### 3. 表面化 -ing 短语
**词汇**: highlighting, underscoring, emphasizing, ensuring, reflecting,
symbolizing, contributing to, cultivating, fostering, encompassing, showcasing

**修复**: 删除 -ing 短语，直接陈述

#### 4. 促销/广告语言
**词汇**: boasts a, vibrant, rich (比喻), profound, enhancing, exemplifies,
commitment to, natural beauty, nestled, in the heart of, groundbreaking (比喻),
renowned, breathtaking, must-visit, stunning

**修复**: 替换为中性描述

#### 5. 模糊归因/骑墙词
**词汇**: Industry reports, Observers have cited, Experts argue, Some critics argue,
several sources/publications

**修复**: 替换为具体来源或删除

#### 6. 模板化「挑战与展望」章节
**词汇**: Despite its... faces several challenges..., Despite these challenges,
Challenges and Legacy, Future Outlook

**修复**: 替换为具体事实叙述

---

### B. 语言语法类

#### 7. 过度使用AI高频词
**词汇**: actually, additionally, align with, crucial, delve, emphasizing, enduring,
enhance, fostering, garner, highlight (动词), interplay, intricate/intricacies,
key (形容词), landscape (抽象), pivotal, showcase, tapestry (抽象),
testament, underscore (动词), valuable, vibrant

**修复**: 用简单/地道词汇替换

#### 8. 回避简单系动词
**词汇**: serves as, stands as, marks, represents [a], boasts, features, offers [a]

**修复**: 换成 is/are/has/have

#### 9. 负面平行结构和尾随否定
**词汇**: Not only...but..., It's not just about..., it's about..., no guessing, no wasted motion

**修复**: 改写为正常从句结构

#### 10. 三点式规则过度使用
**特征**: 强制将信息分成三组（如：keynote sessions, panel discussions, and networking）

**修复**: 自然叙述，不要刻意三等分

#### 11. 优雅变体（同义词循环）
**特征**: 同一主体用不同词反复描述（如 protagonist/main character/central figure/hero）

**修复**: 统一用同一词

#### 12. 虚假范围
**特征**: "from X to Y" 但X和Y不在同一语义尺度上

**修复**: 直接列出具体内容

#### 13. 被动语态和主语缺失片段
**特征**: "No configuration file needed", "Results are preserved automatically"

**修复**: 改为主动语态，加主语

---

### C. 风格类

#### 14. 破折号滥用
**特征**: 大量使用 em dash (—)，模仿「有力」的销售文体

**修复**: 换成逗号、句号或括号

#### 15. 粗体滥用
**特征**: 机械地在标题或列表项中加粗

**修复**: 删除粗体

#### 16. 标题式垂直列表
**特征**: 列表项以粗体标题+冒号开头

**修复**: 融入正文自然叙述

#### 17. 标题中的 Title Case
**特征**: 所有实词首字母大写

**修复**: 改为 Sentence case（仅首词和专有名词大写）

#### 18. Emoji 装饰
**特征**: 🚀💡✅ 等 emoji 装饰标题或要点

**修复**: 删除 emoji

#### 19. 花式引号（Curly Quotes）
**特征**: "" 而非 ""

**修复**: 统一用直引号

---

### D. 交流模式类

#### 20. 协作沟通语气
**词汇**: I hope this helps, Of course!, Certainly!, You're absolutely right!,
Would you like..., let me know, here is a...

**修复**: 删除对话式结语

#### 21. 知识截止日期免责声明
**词汇**: as of [date], Up to my last training update, While specific details are limited,
based on available information

**修复**: 删除

#### 22. 谄媚/奴性语气
**词汇**: Great question!, You're absolutely right!, That's an excellent point!

**修复**: 删除恭维语

---

### E. 填充与修饰类

#### 23. 填充短语
**转换**:
- "In order to achieve this goal" → "To achieve this"
- "Due to the fact that" → "Because"
- "At this point in time" → "Now"
- "In the event that" → "If"
- "The system has the ability to process" → "The system can process"
- "It is important to note that" → 删或替换

#### 24. 过度修饰
**特征**: "could potentially possibly be argued that... might have some effect"

**修复**: 直接陈述

#### 25. 泛化正面结论
**特征**: "The future looks bright. Exciting times lie ahead. A major step in the right direction."

**修复**: 替换为具体未来计划

#### 26. 连字符词组滥用
**词汇**: third-party, cross-functional, client-facing, data-driven, decision-making,
well-known, high-quality, real-time, long-term, end-to-end

**修复**: 不一致地使用或不用连字符

#### 27. 说服性权威套话
**词汇**: The real question is, at its core, in reality, what really matters,
fundamentally, the deeper issue, the heart of the matter

**修复**: 直接陈述观点

#### 28. 引导语和宣告
**词汇**: Let's dive in, let's explore, let's break this down,
here's what you need to know, now let's look at, without further ado

**修复**: 直接开始，不要宣告

#### 29. 碎片化标题
**特征**: 标题后紧跟一行简单重复标题的句子

**修复**: 删除重复句，直接开始正文

---

## 灵魂注入（PERSONALITY AND SOUL）

AI腔文章的核心问题是「没有灵魂」——即使去掉上述所有模式，
如果句式长度完全相同、没有观点、没有第一人称、没有幽默感，仍然不像人写的。

**注入方法**：
1. **有观点** — 不要中立报道，要反应
2. **节奏变化** — 短句+长句交替，不要整齐划一
3. **承认复杂性** — 真实人类有矛盾心理
4. **用「我」** — 适当用第一人称
5. **允许混乱** — 偏题、插入语、不完整的想法是真实的
6. **具体感受** — 不是「这令人担忧」而是「3点夜里代理还在跑让人不安」

---

## 自动修复策略（流水线用）

### 可自动修复
- 词汇替换（7, 14, 15, 18, 23, 26）
- 破折号转逗号/句号（14）
- Emoji 删除（18）
- 被动改主动（13）
- 标题 Title Case → Sentence case（17）
- 模糊引语替换为具体（5, 21）
- 模板章节结构检测（6, 25）

### 不可自动修复（需人工或 MiniMax 重写）
- 三点式规则（10）
- 优雅变体/同义词循环（11）
- 虚假范围（12）
- 碎片化标题（29）
- 灵魂/人格缺失（需人工润色）
- 节奏单一（需人工调整句长）
