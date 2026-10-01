# CharClamp-01 · 炭窑焖烧志

窑场炭窑与焖烧班次台账基线项目（Litestar + SQLAlchemy 2 + Jinja2 + HTMX）。

## 技术栈

| 层 | 技术 |
| --- | --- |
| Web | Litestar · Jinja2 · HTMX CDN · Session 认证 |
| 数据 | SQLAlchemy 2（async） · PostgreSQL 15 |
| 部署 | Docker Compose · Uvicorn |
| 结构 | `domain/` · `infra/` · `web/` 分层（非 Django apps） |

## 路径与端口

- **项目路径**：`d:\work\document\bytecode\claudeCodePro\CharClamp\CharClamp-01`
- **Web**：http://localhost:4750
- **PostgreSQL**：localhost:6150

## 演示账号

| 用户名 | 密码 | 角色 |
| --- | --- | --- |
| `admin` | `123456` | 管理员 |
| `worker` | `123456` | 操作工 |

登录页已预填 `admin` / `123456`。entrypoint 会建表并写入种子数据（窑场 **乌石岗焖烧坞**，窑号如 **坞东-甲 / 坞东-乙 / 河沿-丙**）。

## 主界面：焖烧时间轴

登录后进入全宽 **焖烧时间轴**（不再使用侧栏 + 双 CRUD 列表）：

1. **顶部窑剪影行**：每座炭窑以 SVG 剪影展示，并标注现行容积证温度上限（无证则红字提示「无证 · 请先办证」）；点击某窑用 HTMX 局部刷新下方时间轴，并更新地址栏 `?clamp_id=`；「全部窑」取消筛选。
2. **纵向时间轴**：按开始时间倒序列出 `BurnShift`；每条卡片带窑号徽章（再点可开抽屉）、峰值温度、炭品与当前窑态；「保存峰值」可在侧抽屉改写该班次峰值。
3. **侧抽屉（非独立编辑页）**：「登记班次」写入新班次；点窑徽章打开操作抽屉，可标记「已出炭」（受峰值规则约束）。

## 窑膛容积证

每座炭窑须持有**现行窑膛容积证**（`VolumeCertificate`）。顶栏「窑膛容积证」进入专页，专页含证件台账、新建表单与规则说明。

- **证字段**：炭窑、容积立方米、温度上限摄氏、生效日、签发人、作废时刻（可空）。容积与温度上限都必须为正。
- **一窑一证**：同一炭窑未作废证最多一张——由数据库**部分唯一索引**（`clamp_id` WHERE `revoked_at IS NULL`）兜底，两名操作工并发争抢也只落一张；被挡者看到提示后，时间轴与证专页仍可正常打开。作废旧证后才能为该窑再办新证。
- **办证/作废权限**：操作工可办证（签发人取当前登录人）；**作废权只给管理员**。作废后该证不再约束。
- **峰值约束（无旁路）**：登记班次（抽屉提交）或保存/改写班次峰值（保存接口）时，读取该窑最新未作废证——
  - 无证则拒绝并提示「请先办证」；
  - 峰值若填写，不得超过证面温度上限。
  - 两个入口共用同一条规则与同一句中文，禁止旁路。
- **种子**：坞东-甲无证、坞东-乙温度上限写成 390℃、河沿-丙持 600℃ 现行证，便于演示「无证」与「超上限」。

## 业务规则

炭窑状态不可设为「已出炭」（`drawn`），除非该窑**最近一条** `BurnShift` 的 `peakTempC` 已记录且 **≥ 400℃**。该门槛在容积证约束之外仍然生效，禁止旁路。

规则实现：`src/charclamp/domain/rules.py`

## 端到端自检

无需 Docker 时可用 SQLite 跑全部验收（并发争抢、无证/超上限拒绝、管理员作废、≥400 出炭门槛、页面可达性等 37 项）：

```bash
python e2e_test.py   # 需本机已安装 requirements 及 aiosqlite、httpx
```

## 快速启动

```bash
cd d:\work\document\bytecode\claudeCodePro\CharClamp\CharClamp-01
docker compose up --build
```

浏览器打开 http://localhost:4750

## 目录结构

```
CharClamp-01/
├── docker-compose.yml
├── Dockerfile
├── entrypoint.sh
└── src/charclamp/
    ├── main.py
    ├── domain/          # models + rules
    ├── infra/           # db + seed + security
    └── web/             # controllers + templates + static
```
