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

1. **顶部窑剪影行**：每座炭窑以 SVG 剪影展示；点击某窑用 HTMX 局部刷新下方时间轴，并更新地址栏 `?clamp_id=`；「全部窑」取消筛选。
2. **纵向时间轴**：按开始时间倒序列出 `BurnShift`；每条卡片带窑号徽章（再点可开抽屉）、峰值温度、炭品与当前窑态。
3. **侧抽屉（非独立编辑页）**：「登记班次」写入新班次；点窑徽章打开操作抽屉，可标记「已出炭」（受峰值规则约束）、查看现行容积证并改写最近班次峰值。
4. **容积证专页**：顶栏「容积证」进入，含全部证件列表、新建容积证、管理员作废；顶栏「时间轴」返回主界面。

## 业务规则

1. **窑膛容积证**：每座炭窑须有现行（未作废）容积证，证字段为 炭窑 / 容积立方米 / 温度上限摄氏 / 生效日 / 签发人 / 作废时刻（可空）；容积与温度上限都必须为正。
   - 操作工可办证；**作废权仅管理员**。作废后该证不再约束。
   - 同一窑未作废证最多一张（数据库部分唯一索引强制，两名操作工抢证只落一张）。
2. **班次峰值受证约束**：登记或改写班次峰值时，读取该窑最新未作废证：
   - **无证**：拒绝并提示「该炭窑尚无现行窑膛容积证，请先办证后再登记班次峰值」；
   - **超上限**：峰值若填写，不得超过证面温度上限，否则拒绝。
   - 抽屉提交（`/shifts/new`）与保存接口（`/shifts/{id}/peak`）共用同一句中文校验，禁止旁路。
3. **已出炭门槛不放宽**：标记「已出炭」仍要求该窑最近一条 `BurnShift` 的 `peakTempC` 已记录且 **≥ 400℃**，与容积证校验相互独立。

规则实现：`src/charclamp/domain/rules.py`；容积证页面：顶栏「容积证」（列表 / 新建 / 管理员作废），窑剪影直接显示现行上限（无证标红）。

种子数据：**坞东-乙 无证**（演示无证拦截）；**坞东-甲 上限写成 390℃**（演示超上限拦截，填 391℃ 及以上即被拒）。

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
