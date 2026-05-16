# Trade Relay — 使用教程

## 目录

1. [项目简介](#1-项目简介)
2. [技术架构](#2-技术架构)
3. [环境要求](#3-环境要求)
4. [安装步骤](#4-安装步骤)
5. [配置说明](#5-配置说明)
6. [启动项目](#6-启动项目)
7. [功能使用指南](#7-功能使用指南)
8. [API 接口说明](#8-api-接口说明)
9. [打包发布](#9-打包发布)
10. [常见问题](#10-常见问题)

---

## 1. 项目简介

**Trade Relay** 是一个多用户桌面交易终端，支持通过 Binance 合约账户下单、查看持仓、管理用户与配置。

### 核心功能

| 功能 | 说明 |
|------|------|
| **Binance 面板** | 内嵌 Binance 合约交易页面，通过预加载脚本实时抓取 OHLCV、资金费率等行情数据 |
| **下单面板** | 支持限价/市价、做多/做空、全仓/逐仓，可设置止盈止损 |
| **持仓面板** | 四标签页：当前持仓 / 当前委托 / 历史订单 / 成交历史 |
| **用户管理** | 管理员可创建、编辑、停用用户账号并配置各自的 Binance API 密钥 |
| **个人资料** | 展示交易统计（总 PnL、胜率、手续费）及日 PnL 柱状图 |
| **系统配置** | 每位用户独立存储 API Key/Secret，支持测试网与模拟下单模式 |

---

## 2. 技术架构

```
Trade Relay
├── Electron (桌面壳)
│   ├── main.js          — 主进程：窗口管理、BrowserView、IPC、JWT 存储
│   ├── preload.js       — 渲染进程桥接：暴露 window.electronAPI
│   └── binance-preload.js — 注入 Binance 页面：抓取 WS 数据、扩展图表
│
├── React + Vite (前端 UI，端口 5173)
│   ├── src/store/       — Zustand 状态：authStore、marketStore
│   ├── src/api/         — axios API 客户端（调用 FastAPI）
│   ├── src/hooks/       — useMarketData（监听 IPC 行情事件）
│   └── src/components/
│       ├── LoginScreen      — 登录界面
│       ├── TitleBar         — 导航栏 + 窗口控件
│       ├── BinancePanel     — BrowserView 占位层
│       ├── OrderFormWidget  — 下单表单
│       ├── PositionsPanel   — 持仓 / 委托 / 历史四标签页
│       ├── AdminScreen      — 用户管理（仅管理员）
│       ├── ProfileScreen    — 个人统计 + 日 PnL 图
│       └── ConfigScreen     — API 密钥设置
│
└── FastAPI (Python 后端，端口 8000)
    ├── /api/auth        — 登录 / 验证 Token
    ├── /api/users       — 用户 CRUD（管理员）
    ├── /api/orders      — 下单 / 委托历史
    ├── /api/positions   — 持仓查询
    ├── /api/config      — 用户 Binance 配置
    └── /api/profile     — 交易统计 / 日 PnL
```

### 数据流

```
Binance 网页 (BrowserView)
  │  WS 数据（价格 / K 线 / 资金费率）
  ▼
binance-preload.js
  │  ipcRenderer.send('market-data', payload)
  ▼
Electron main.js
  │  mainWindow.webContents.send('market-data', payload)
  ▼
React useMarketData() hook → marketStore (Zustand)
  │
  ▼
TickerWidget / OrderFormWidget / PositionsPanel 实时刷新

用户操作（下单 / 登录 / 配置）
  │  window.electronAPI.login() / axios POST /api/orders
  ▼
Electron IPC → httpRequest() → FastAPI
  │  python-binance → Binance REST API
  ▼
MySQL（持久化订单 / 持仓 / 用户）
```

---

## 3. 环境要求

| 依赖 | 最低版本 |
|------|---------|
| Node.js（含 npm） | v18+ |
| Python | 3.10+ |
| MySQL | 5.7+ / 8.0+ |

```bash
# 验证版本
node -v     # >= 18.0.0
python3 -V  # >= 3.10.0
mysql -V
```

---

## 4. 安装步骤

### 4.1 克隆仓库

```bash
git clone <repo-url>
cd trade-relay
```

### 4.2 安装前端依赖

```bash
npm install
```

### 4.3 创建 Python 虚拟环境并安装后端依赖

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
```

### 4.4 创建数据库

```sql
-- 在 MySQL 中执行
CREATE DATABASE trade_relay CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'trade_relay'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON trade_relay.* TO 'trade_relay'@'localhost';
FLUSH PRIVILEGES;
```

> 后端启动时会自动执行 `init_db()` 建表，无需手动导入 SQL。

---

## 5. 配置说明

### 5.1 环境变量（`.env` 文件）

在项目根目录创建 `.env`：

```env
# MySQL 连接
TRADE_RELAY_MYSQL_HOST=127.0.0.1
TRADE_RELAY_MYSQL_PORT=3306
TRADE_RELAY_MYSQL_USER=trade_relay
TRADE_RELAY_MYSQL_PASSWORD=your_password
TRADE_RELAY_MYSQL_DATABASE=trade_relay
TRADE_RELAY_MYSQL_POOL_SIZE=8

# JWT 签名密钥（请修改为随机长字符串）
TRADE_RELAY_JWT_SECRET=change-me-to-a-random-secret-string

# API 密钥加密主密钥
TRADE_RELAY_ENCRYPTION_KEY=another-random-key-for-aes

# Binance 面板设置（可选）
BINANCE_LANG=zh-CN
BINANCE_SYMBOL=BTCUSDC
BACKEND_PORT=8000

# 代理设置（可选，供 Electron/Chromium 网络栈使用）
ALL_PROXY=socks5://127.0.0.1:10808
# HTTPS_PROXY=http://127.0.0.1:10809
```

远端 MySQL 或经代理访问 MySQL 时，建议保留 `TRADE_RELAY_MYSQL_POOL_SIZE` 大于 `0`，让应用复用已建立连接，避免每次请求都承担完整的建连耗时。默认值为 `8`。

### 5.2 手动初始化管理员账号

当前版本不会在启动时自动创建管理员账号。首次部署时，请先手动向 `users` 表插入一条管理员记录。

**步骤 1：生成密码哈希**

```bash
cd /path/to/trade-relay
source .venv/bin/activate
python scripts/hash_password.py
```

脚本会提示输入两次密码，并输出一段 bcrypt 哈希。你也可以直接传参：

```bash
python scripts/hash_password.py 'your-strong-password'
```

**步骤 2：插入管理员账号**

将上一步得到的哈希填入下面 SQL：

```sql
INSERT INTO users (username, password_hash, role, is_active)
VALUES ('admin', '$2b$12$replace_with_bcrypt_hash', 'admin', 1);
```

如果你使用的是项目自带表结构，也可以额外写入空的 API 凭证列：

```sql
INSERT INTO users (username, password_hash, role, is_active, binance_api_key, binance_api_secret)
VALUES ('admin', '$2b$12$replace_with_bcrypt_hash', 'admin', 1, NULL, NULL);
```

执行后，可用这条 SQL 确认管理员账号已存在：

```sql
SELECT id, username, role, is_active FROM users WHERE username = 'admin';
```

建议只在首次部署时手动插入一次管理员账号，后续普通用户通过系统内的「用户管理」页面创建。

### 5.3 用户 Binance API 配置

每位用户的 API Key 有两种存储方式（优先级从高到低）：

1. **前端「系统配置」页** — 存储在 `configs/users/<username>.yaml`
2. **环境变量** — `TRADE_RELAY_BINANCE_API_KEY` / `TRADE_RELAY_BINANCE_API_SECRET`

```yaml
# configs/users/alice.yaml 示例
binance:
  api_key: "your-api-key"
  api_secret: "your-api-secret"
  testnet: false
trading:
  mock_mode: false
```

---

## 6. 启动项目

### 6.1 开发模式（推荐）

需要两个终端同时运行：

**终端 1 — Python 后端**

```bash
cd /path/to/trade-relay
source .venv/bin/activate
python main.py --reload --port 8000
```

**终端 2 — Electron + React**

```bash
cd /path/to/trade-relay
npm run dev
```

`npm run dev` 会同时启动 Vite 开发服务器（端口 5173）和 Electron，Electron 会等待 Vite 就绪后自动打开桌面窗口。

### 6.2 一键启动脚本（可选）

```bash
#!/bin/bash
# start.sh
source .venv/bin/activate
python main.py --port 8000 &
npm run dev
```

### 6.3 验证后端是否正常

```bash
curl http://localhost:8000/health
# 预期输出: {"status":"ok","service":"Trade Relay Backend"}
```

---

## 7. 功能使用指南

### 7.1 登录

启动后自动显示登录界面。请使用预先手动初始化的数据账号登录。Token 会通过 Electron `safeStorage` 加密存储，下次启动自动恢复登录状态。

### 7.2 交易面板

主界面分为三个区域：

```
┌──────────────────────────────┬──────────────┐
│                              │              │
│     Binance 合约页 (70%)     │  下单面板    │
│     （真实 Binance 网页）    │  (30%)       │
│                              │              │
├──────────────────────────────┤              │
│     持仓 / 委托 / 历史       │              │
└──────────────────────────────┴──────────────┘
```

**下单步骤：**

1. 在 Binance 面板选择合约品种（URL 会自动同步）
2. 在下单面板选择：全仓 / 逐仓、开仓 / 平仓、限价 / 市价
3. 填写数量、价格（限价单），可选填止盈 / 止损
4. 点击 **买入（做多）** 或 **卖出（做空）**

**图表全屏：** 点击标题栏「扩展图表」按钮，Binance K 线图将自动展开至全屏。

### 7.3 用户管理（管理员）

点击导航栏「用户管理」（仅管理员可见）：

- **新建用户** — 填写用户名、密码、角色，可同时绑定 Binance API Key
- **编辑用户** — 修改角色、重置密码、更新 API Key
- **停用用户** — 停用后该用户无法登录（不删除历史数据）

### 7.4 个人资料

点击导航栏「个人资料」查看：

- 总已实现 PnL（按现金流近似计算）
- 胜率、总成交笔数、总手续费
- 按日期的 PnL 柱状图（绿涨红跌）

### 7.5 系统配置

点击导航栏「系统配置」：

- 填写当前登录用户的 Binance API Key / API Secret
- 勾选「测试网」使用 Binance Testnet 下单
- 勾选「模拟下单」仅记录订单到数据库，不实际发送至交易所

---

## 8. API 接口说明

所有接口需要在请求头携带 JWT Token（登录后由 Electron 自动附加）：

```
Authorization: Bearer <token>
```

### 8.1 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/auth/login` | 登录，返回 `access_token` 和用户信息 |
| `GET` | `/api/auth/me` | 验证 Token，返回当前用户信息 |

**登录请求体：**
```json
{ "username": "your-username", "password": "your-password" }
```

**登录响应：**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user": { "id": 1, "username": "admin", "role": "admin", "is_active": true }
}
```

### 8.2 用户管理（需管理员权限）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/users` | 获取所有用户列表 |
| `POST` | `/api/users` | 创建新用户 |
| `PATCH` | `/api/users/{id}` | 更新用户（角色 / 密码 / API Key / 状态） |
| `DELETE` | `/api/users/{id}` | 停用用户 |

### 8.3 订单

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/orders` | 提交新订单 |
| `GET` | `/api/orders/active` | 获取当前委托 |
| `GET` | `/api/orders/history` | 获取历史订单 |
| `GET` | `/api/orders/fills` | 获取平台成交记录 |

**下单请求体：**
```json
{
  "symbol": "BTCUSDC",
  "side": "BUY",
  "order_type": "LIMIT",
  "quantity": 0.001,
  "price": 60000.0
}
```

### 8.4 持仓

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/positions` | 获取当前持仓（普通用户只看自己，管理员看全部） |

### 8.5 配置

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/config/me` | 读取当前用户配置 |
| `POST` | `/api/config/me` | 保存当前用户配置 |

### 8.6 个人资料

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/profile/stats` | 交易统计（总 PnL / 胜率 / 笔数 / 手续费） |
| `GET` | `/api/profile/daily-pnl` | 按日 PnL 列表（用于图表） |

---

## 9. 打包发布

### 9.1 Linux AppImage

```bash
# 先确保后端依赖已安装
source .venv/bin/activate

# 构建前端 + 打包 Electron
npm run build:linux
```

产物位于 `dist-electron/`。

### 9.2 Windows

```bash
npm run build:win
```

> Windows 打包需要在 Windows 环境或 Wine 中进行。

### 9.3 生产环境注意事项

- 将 `.env` 中 `TRADE_RELAY_JWT_SECRET` 替换为至少 32 位随机字符串
- 将 `TRADE_RELAY_ENCRYPTION_KEY` 替换为独立的随机密钥（不要与 JWT Secret 相同）
- 确保 MySQL 用户仅有 `trade_relay` 数据库的权限，不要使用 `root` 账号
- 建议对 `:8000` 端口加防火墙规则，仅允许 `localhost` 访问

---

## 10. 常见问题

### Q: 打开 Binance 面板时白屏 / 无法加载

**原因：** BrowserView 需要网络访问 `www.binance.com`。  
**解决：** 确认网络连通性，必要时配置系统代理。Electron 会自动应用系统代理设置。

### Q: 登录后提示 "Invalid or expired token"

**原因：** Token 已过期（默认有效期 72 小时）或 JWT Secret 已变更。  
**解决：** 退出登录后重新登录，或检查 `.env` 中 `TRADE_RELAY_JWT_SECRET` 是否与后端启动时一致。

### Q: 下单返回 "No API key configured"

**原因：** 当前用户未配置 Binance API Key。  
**解决：** 进入「系统配置」页填写 API Key 和 API Secret，或由管理员在用户管理中配置。

### Q: 后端启动报 `Can't connect to MySQL server`

**原因：** MySQL 未启动或连接参数错误。  
**解决：**
```bash
# 检查 MySQL 服务
sudo systemctl status mysql

# 验证连接参数
mysql -u trade_relay -p -h 127.0.0.1 trade_relay
```

### Q: 如何开启 Binance 测试网

1. 访问 [testnet.binancefuture.com](https://testnet.binancefuture.com) 获取测试网 API Key
2. 在「系统配置」中填入测试网 API Key，并勾选「测试网」
3. 重新提交订单，订单将发送至测试网

### Q: `npm run dev` 启动后 Electron 窗口没有出现

**原因：** Vite 尚未就绪，Electron 正在等待 `http://localhost:5173`。  
**解决：** 等待终端输出 `Local: http://localhost:5173/` 后约 2 秒，Electron 窗口会自动弹出。

---

*文档版本：v1.0.1 — 2026-05*
