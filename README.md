# 法序 · 智慧法律咨询平台

Vue 3 + **JavaScript**、FastAPI、MySQL、Chroma、百炼云端模型。五个业务智能体独立配置模型，协作咨询通过 LangGraph 调用两个业务智能体。

## 客户功能

- 账号注册、登录、退出、昵称修改与密码修改。
- 上传图片，拖动、缩放裁剪头像；后端校验并统一保存为 256 × 256 JPEG。
- 五类业务咨询及协作咨询，回答引用可展开原文。
- 按账号保存咨询记录，支持选择历史会话、继续追问、新建及删除会话。
- 在个人中心主动保存或删除咨询背景、回答偏好，按用户和业务范围用于后续咨询。
- 资料入库与切分合并为同一工作台：左侧上传和资料列表，右侧切分设置与预览；另设资料检索入口。
- 上传范围支持“通用法规”：个人资料供当前账号的所有业务智能体检索，管理员可另行选择公共资料供所有账号检索。选择文件后根据文件名和正文关键词推荐范围并说明理由，点击“采用推荐”才应用，仍可手动改选。推荐接口只分析、不保存文件、不创建入库任务，不代表法规效力已核验。
- 处理记录通过刷新左侧的按钮展开。左上绿色显示未读完成数，右上红色显示未读失败数，右下黄色显示排队和处理中数量；查看后清除已读的完成和失败提醒，待处理数量随任务状态自动更新。已读标记按账号保存在当前浏览器，不包含登录凭据。

前端仅显示客户功能，不提供数据库初始化或服务配置入口，不展示模型名称、运行编号等维护信息。公共法律资料由后台维护，普通用户不能将私人材料发布为公共资料。

## 账号与隔离

管理员通过本机维护脚本配置，普通注册接口始终创建普通用户：

```powershell
.\.venv\Scripts\python.exe scripts/manage_admin.py admin
```

账号存在时保留原密码并升级权限；不存在时交互输入并确认新密码，仅保存密码哈希。配置后重新登录。管理员上传界面可选择“公共资料”或“个人资料”；公共资料选择“通用法规”并确认入库后可供所有账号的智能体检索，普通用户不能上传公共资料或修改他人的公共文档。管理员只能维护自己上传的文档。

管理员可通过资料列表的“来源/版本”编辑来源链接、版本说明、本版施行日期、效力状态和核验记录，无需重新入库。登记非“尚未核验”状态必须提供来源、版本和核验人/时间；不会自动推断法规现行性。此入口不能修改正文、所有者、公开范围或索引，也不替代完整的版本沿革与审计系统。

密码使用带随机盐的 scrypt 哈希。登录态由服务端保存，浏览器使用 HttpOnly、SameSite Cookie，不在 localStorage 或 sessionStorage 保存登录凭据。退出会撤销当前会话，修改密码会撤销其他会话。

资料、会话、记忆和运行记录均按后端认证得到的用户 ID 查询。客户端不能指定其他用户身份。历史开发数据不会自动分配给新注册用户。保存的记忆属于用户主动提供的背景，不作为法律依据，也不覆盖系统指令。

密码找回采用一次性找回码，无需短信或邮件服务：注册成功时显示一次；已有账号请到“个人中心 → 账号安全 → 密码找回码”，输入当前密码后生成并妥善保存。生成新码会作废旧码。登录页点击“忘记密码”，输入账号、找回码和至少 8 位的新密码；成功后全部旧登录失效，找回码作废，登录后可重新生成。服务端仅保存找回码的 SHA-256 哈希。没有找回码且忘记密码时，当前界面不能自助恢复，请联系维护人员核实身份处理。

普通用户可在“个人中心 → 账号安全 → 注销账号”，输入当前密码与完整账号名并再次确认。正在处理资料或咨询时暂不允许注销；管理员不支持自助注销。注销立即删除账号、登录会话、找回码、记忆、咨询消息及关联运行记录，私人文档先标记删除并退出检索，再由后台任务清理原文件、预览、分段、向量与头像。清理任务持久保存，文件删除失败会重试；请保持 worker 运行。数据库保留不含原文的文档删除占位和处理任务。公共资料与其他用户数据不受影响。

新建的独立检索日志已关联用户，注销时可删除；升级前没有记录用户身份的独立检索日志无法可靠归属，不属于自动清理范围。短信或邮件验证及多实例限流不在本次收尾范围。

## 环境与启动

Windows 下可直接双击项目根目录的 `启动服务.bat`、`关闭服务.bat` 和 `服务状态.bat`。启动脚本检查 MySQL 连接，隐藏启动 Chroma、API、后台任务 worker 和前端，等待服务就绪；重复点击不会重复启动。关闭脚本只关闭本次管理的项目进程树，不关闭远程 MySQL、不删除资料。日志保存在 `.cache/services/`，运行中请勿删除该目录。先前手工启动的服务需要在原终端关闭，脚本不会按端口强行结束未知进程。

命令行方式：

```powershell
.\.venv\Scripts\python.exe scripts/services.py start
.\.venv\Scripts\python.exe scripts/services.py status
.\.venv\Scripts\python.exe scripts/services.py stop
```

MySQL 位于独立虚拟机，仍需先启动虚拟机和 MySQL。上述脚本依赖已安装的项目 `.venv`、Node.js 及 `frontend/node_modules`，不会自动修改数据库或安装依赖。关闭采用进程组结束服务，应先等待正在进行的咨询和入库任务完成；未完成的入库任务在再次启动后按队列租约恢复。

需要 Python 3.11+、Node.js 22+、MySQL 8、Chroma。当前本机使用项目 `.venv`，复用 ai0707 环境的基础包；新机器请创建干净环境。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe scripts/setup_local.py
```

本项目 `.env` 保存独立数据库连接；百炼凭据从共享 `../../.env` 读取，其他项目的数据库或模型设置不自动继承。不要将真实凭据提交到仓库。

数据库使用独立 `legal_assistant` 库和专用账号。现有服务器已创建库和表，DBA 授权已完成；API 与 worker 均使用专用账号，真实客户接口闭环已通过。授权语句留存于 [授权 SQL](scripts/grant_legal_account.sql)，不使用管理账号作为常驻服务身份。

初始化或添加本轮新表在终端执行，不通过客户端页面：

```powershell
.\.venv\Scripts\python.exe scripts/migrate.py
```

当前迁移仅新增缺失的表，不删除旧数据，不修改旧表；后续旧表结构变更需正式迁移，不能靠 create_all 更新。

依次在独立终端启动：

```powershell
.\.venv\Scripts\python.exe scripts/run_chroma.py run --path ./data/chroma --host 127.0.0.1 --port 8100
```

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8091
```

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend')
.\.venv\Scripts\python.exe -m app.worker
```

```powershell
cd frontend
npm ci
npm run dev
```

打开 [客户工作台](http://127.0.0.1:5174/)，注册账号即可使用，无需填写其他凭据。

部署到 HTTPS 域名时，将 `.env` 的 COOKIE_SECURE 设置为 true，并在 ALLOWED_ORIGINS 配置实际客户站点来源。开发端通过 Vite 同源代理转发 `/api`，部署时 Nginx 转发并禁用 SSE 缓冲。不要直接把开发服务公开到互联网。

## 数据结构

| 存储 | 内容 |
|---|---|
| account_recovery、account_file_cleanup | 一次性找回码哈希与待清理文件路径 |
| users、user_sessions | 账号、密码哈希、昵称、头像版本、哈希后的登录会话 |
| conversations、chat_messages | 用户所属会话、业务范围、消息与运行关联 |
| user_memories | 用户主动保存的背景、偏好及适用范围 |
| documents、chunk_previews、chunks | 资料来源、版本、原文、分段、位置与索引标识 |
| index_jobs | 资料处理、重试与删除任务 |
| agent_runs、retrieval_logs | 问答结果、引用及检索追溯 |
| Chroma | 文本块向量与索引元数据 |
| data/originals、data/avatars | 上传原文件与重新编码后的头像 |

SQL 与向量索引通过持久任务协调。删除资料后先退出新检索，再清理原文和派生数据。历史回答若引用已删除来源，会清理保存的来源内容；删除记忆后不再用于新咨询。

资料任务长时间处于“排队中”时，先检查独立启动的 `app.worker` 进程是否仍在运行。仅启动 API 和前端不会消费队列。Worker 遇到数据库启动失败、轮询断连或无法保存任务失败状态时，会等待 5 秒后重连；未保存结果的运行任务通过租约到期机制恢复。连接异常日志不输出凭据。

## 验证

检索已支持中文 BM25 + 向量召回、RRF融合、特征重排序和低相关过滤；默认参数、权限边界及真实服务对照结果见 [混合检索与过滤验证](docs/混合检索与过滤验证.md)。可运行 `.\.venv\Scripts\python.exe scripts/compare_retrieval.py --live` 复现；不需要重新向量化已有资料。

五领域公共法规问答基线：`scripts/evaluate_legal_baseline.py --validate` 离线核对题目与来源哈希；`--live` 实际执行检索、生成和无依据题，自动清理临时测试账号，原始报告保存至 `data/eval-reports/`。首轮结果和质量边界见 [问答质量首轮验收报告](docs/问答质量首轮验收报告.md)，不将命中率等同于法律正确率。

扩展验收使用 `--suite extended`，每域10题、共50题，支持 `--concurrency 1/2/3` 及 `--cases 用例ID...` 专项复测；结果见 [第二轮验收报告](docs/问答质量第二轮验收报告.md)。引用修复后仍有不合格说明时只保留逐项校验通过的内容，标明部分完成或不发布结论，不自动认定整份回答已通过法律审核。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
cd frontend
npm test
npm run build
```

离线 SQL 测试使用临时 SQLite，外部模型和向量接口被模拟，不改变正常应用的 MySQL 选型。

实际 API、后台 worker、向量服务和模型的客户流程验证：

```powershell
.\.venv\Scripts\python.exe scripts/verify_customer_flow.py --execute
```

此测试要求 API 和 worker 已启动，创建临时账号及虚构资料，验证注册登录、记忆、自动入库、检索、带引用回答、历史恢复与自动删除，结束后清理测试账号和记录。

真实 MySQL 账号隔离测试创建并清理临时应用用户，无模型调用：

```powershell
.\.venv\Scripts\python.exe scripts/smoke_accounts.py --execute
```

密码找回及注销的真实服务验证（创建临时账号和虚构资料，不调用模型）：

```powershell
.\.venv\Scripts\python.exe scripts/smoke_account_lifecycle.py --execute
```

模型兼容性和完整 RAG 测试需显式启用，使用虚构演示材料，不代表法律质量验收：

```powershell
.\.venv\Scripts\python.exe scripts/probe_models.py --live
.\.venv\Scripts\python.exe scripts/live_smoke.py --live
```

维护脚本支持显式 `--admin-env` 进行一次性、仅限法律项目库的联调；管理凭据仅在进程内使用，不保存。正常服务使用专用账号。

## 部署与待办

提供 [Compose](compose.yaml)、Dockerfile 和 Nginx 配置，本机无 Docker，Compose 未实机验证。默认端口只绑定本机：前端 5174、API 8091、Chroma 8100、MySQL 3306。已有数据卷不会因环境变量改变而自动重建数据库账号。

仍待完成：正式法律资料与标准证据集、法律版本沿革及过渡适用管理、OCR、原 PDF 高亮、语义引用支持评测、协作冲突解释、全面故障恢复、传递依赖锁定及多实例部署防护。引用出处检查不能替代人工法律审核。

[需求分析](docs/智慧法律咨询平台项目需求分析.md) · [规划书](docs/智慧法律咨询平台项目规划书.md) · [开发记录](docs/开发记录.md)
