# KaiCrawl · 爬虫管理系统

> 智能数据采集与文档生成平台 —— 多源采集 · 定时抓取 · 自动成稿 · 按规归档

基于 Flask 的本地化新闻采集系统：可视化配置数据源与定时任务，按统一格式规范自动生成 WORD，并按 **分类 / 日期** 目录归档。界面采用 Dify 风格科技蓝设计，支持浅色 / 暗色双主题。

---

## ✨ 功能特性

- **多数据源采集**：内置 9 个权威数据源，按域名自动指派解析器插件；卡片式可视化管理（抓取 / 覆盖 / 启停 / 编辑）。
- **定时任务调度**：APScheduler 后台调度 + SQLAlchemy 持久化作业，重启不丢任务；支持「立即运行」「按日期回溯抓取」。
- **WORD 自动生成**：严格按格式规范生成 —— 标题加粗居中、正文首行缩进两字、宋体五号、作者策略、图片按原文位置插入。
- **分类 / 日期归档**：输出到 `data/output/<分类>/<YYYY-MM-DD>/<标题>.docx`，图片落同目录 `images/`。
- **运行日志**：完整的抓取执行记录，支持 **搜索 / 筛选 / 分页 / 刷新**，并可一键 **停止运行中的任务**。
- **权限管理（RBAC）**：菜单树 + 角色 + 人员 + 按钮级权限；登录 / 操作审计日志。
- **手动导入**：适用于公众号、学习强国等无法自动抓取的来源，粘贴正文即可生成 WORD。
- **双主题界面**：科技蓝主色，浅色 / 暗色一键切换，响应式布局。

---

## 🧰 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Flask 3、Flask-SQLAlchemy、APScheduler、waitress |
| 解析 | requests、BeautifulSoup、lxml |
| 文档 | python-docx、openpyxl、Pillow |
| 前端 | Jinja2 + Bootstrap 5 + Bootstrap Icons（自研主题） |
| 存储 | SQLite（业务表 + APScheduler 作业表） |

---

## 🚀 快速开始

```bash
# 1. 克隆
git clone https://github.com/kaifuu/kaicrawl.git
cd kaicrawl

# 2. 安装依赖（建议先建虚拟环境）
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt

# 3. 启动
python run.py
```

浏览器访问 **http://127.0.0.1:5000/auth/login**

**默认账号**：`admin` / `admin123`（首次登录后请及时修改密码）

### 首次启动自动完成

- 建库建表（SQLite，位于 `data/crawler.db`）
- 从 `爬虫数据来源.xlsx` 种子导入 **9 个数据源**
- 创建 `admin` 超管角色、RBAC 菜单树（幂等，可重复执行）

---

## 📋 使用流程

1. **采集**：「数据源管理」→ 找到来源 → 点 **抓取**（可选填起始日期回溯历史）。
2. **定时**：「任务管理」→ 为某来源新建每日定时任务（`HH:MM`），或「立即运行」。
3. **查看**：「文章文件」按分类 / 来源 / 日期筛选，下载生成的 WORD。
4. **监控**：「运行日志」查看每次抓取的状态、新增数与耗时；运行中可点 **停止**。
5. **手动导入**：「手动导入」粘贴标题与正文，生成 WORD 并归入对应分类当天目录。

---

## 📁 目录结构

```
kaicrawl/
├── run.py                    # 入口（waitress 提供服务，回退 Flask 开发服务器）
├── config.py                 # 路径与抓取配置
├── requirements.txt
├── 爬虫数据来源.xlsx          # 数据源规范（启动种子导入）
├── app/
│   ├── __init__.py           # 应用工厂
│   ├── models.py             # Source / Task / Article / CrawlLog / RBAC 模型
│   ├── crawler.py            # 抓取编排（列表→去重→详情→生成WORD→落库，含停止信号）
│   ├── docx_writer.py        # WORD 生成（格式规范）
│   ├── excel_sync.py         # Excel → 数据源
│   ├── scheduler_jobs.py     # APScheduler 集成（持久化 + 立即运行）
│   ├── captcha.py            # 登录验证码
│   ├── auth.py               # 全局登录保护 + 操作日志 + 权限装饰器
│   ├── security_seed.py      # 菜单 / 角色 / 管理员 幂等种子
│   ├── sources/              # 解析器插件（每站一文件）
│   └── routes/               # 仪表盘 / 数据源 / 任务 / 文章 / 日志 / 管理 路由
├── templates/                # Jinja2 中文界面（Bootstrap 5 + 自研主题）
├── static/css/theme.css      # Dify 风格双主题样式
└── data/
    ├── crawler.db            # SQLite（运行时生成，已 gitignore）
    └── output/               # WORD 输出根目录（运行时生成，已 gitignore）
```

---

## 🔌 解析器插件

每个站点一个解析器，继承 `GenericGovParser`（CSS 选择器 + 启发式），实现 `fetch_list()` / `fetch_detail()`，按 `parser_key` 注册。**站点结构变更只需调整对应插件文件**，不影响其它来源。

| parser_key | 站点 | 状态 |
|---|---|---|
| `bjdch` | 北京东城政府网 bjdch.gov.cn | 列表 + 正文可用 |
| `people` | 人民网 cpc.people.com.cn | 列表 + 正文可用 |
| `dangjian` | 党建网 dangjian.cn | 可用（栏目列表为前端 JS 渲染，已实现读取其预生成 JS 数据文件） |
| `xuexi` | 学习强国 | 反爬 / JS 渲染，请用「手动导入」 |
| `wechat` | 微信公众号 | 无公开 Web 列表，请用「手动导入」或 URL 批量导入 |
| `wechat_rss` | 微信公众号（RSS） | 视 RSS 源可用性 |

> **党建网说明**：其栏目页（如「学史明理」`list_50765_1.html`）的文章列表由前端 JS 动态注入，静态 HTML 为空壳。`dangjian` 解析器直接读取站点预生成的 JS 数据文件（`/js/{栏目id}/mi4_page_articles_guide.js` → `mi4_sub_articles_*.js`）获取列表，并兼容其 `<div>` 段落式正文。

---

## ⚙️ 抓取行为

- **去重**：同来源按 URL 去重；文件被人工删除视为未归档，下次自动修复重生成。
- **覆盖抓取**：删旧记录与旧 WORD 后重新抓取，避免文件名 `_2/_3` 堆积。
- **回溯抓取**：填写起始日期则翻页回溯至该日期（仅支持可翻页解析器）。
- **礼貌抓取**：每篇间隔 `0.8s`（`config.REQUEST_DELAY`），单次最多 `30` 篇（`MAX_ARTICLES_PER_RUN`）。
- **可停止**：运行中的任务收到停止信号后，在当前文章处理完成后安全中断，日志标记为「已停止」。

## 📝 WORD 格式规范

- **标题**：加粗 · 宋体 · 五号(10.5pt) · 居中 · 单倍行距
- **正文**：首行缩进 2 字符(`firstLineChars=200`) · 宋体 · 五号 · 左对齐 · 单倍行距
- **作者**：`单位动态` 分类固定为「各单位」；其余用原文作者，无则留空
- **图片**：png/jpg 按原文位置插入，并单独保存到当天目录 `images/` 子目录

---

## 🔧 维护备注

- **重置数据库**：删除 `data/crawler.db` 后重启，将重建并重新种子导入。
- **时区**：调度使用 `Asia/Shanghai`。
- **数据目录**：`data/crawler.db` 与 `data/output/` 为运行时生成，已在 `.gitignore` 中排除。

---

## 📄 许可

本项目用于内部数据采集与文档归档，按需自行选择许可协议。
