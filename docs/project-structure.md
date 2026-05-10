# Trade Relay — 项目结构文档

> 多用户交易桌面终端，基于 Binance API，使用 [Textual](https://textual.textualize.io/) 构建 TUI 界面。

---

## 目录结构

```
trade-relay/
├── main.py                          # 程序入口
├── requirements.txt                 # Python 依赖清单
├── pyproject.toml                   # 包元数据与构建配置
├── trade_relay.spec                 # PyInstaller 打包配置（Windows）
│
├── scripts/
│   ├── setup.sh                     # Linux/macOS 一键初始化（创建 .venv + 安装依赖）
│   └── build_windows.bat            # Windows 初始化 + PyInstaller 打包
│
├── configs/
│   └── users/
│       ├── README.md                # 用户配置说明
│       ├── admin.yaml               # admin 用户的 Binance 配置
│       └── {username}.yaml          # 每位用户的独立配置文件
│
├── data/
│   └── trade_relay.db               # SQLite 数据库（运行时自动生成）
│
└── trade_relay/
    ├── __init__.py                  # 包声明，版本号
    ├── i18n.py                      # 国际化模块
    ├── database.py                  # 数据库层（SQLite CRUD）
    ├── config.py                    # 用户配置读写（YAML）
    │
    ├── auth/
    │   ├── __init__.py
    │   └── manager.py               # 认证管理：登录、用户增删改
    │
    ├── trading/
    │   ├── __init__.py
    │   ├── binance_client.py        # Binance API 封装（真实/测试网/模拟）
    │   └── order_manager.py         # 下单流程：校验 → 执行 → 持久化
    │
    └── ui/
        ├── __init__.py
        ├── styles.tcss              # Textual 深色主题 CSS
        ├── app.py                   # 顶层 App（屏幕路由）
        ├── login_screen.py          # 登录界面
        ├── main_screen.py           # 主界面（含 Tabs）
        ├── admin_screen.py          # 用户管理界面（仅管理员可见）
        └── config_screen.py         # Binance API Key 配置界面
```

---

## 模块说明

### `main.py` — 程序入口

```
启动流程：init_db() → ensure_admin_exists() → TradeRelayApp().run()
```

初始化数据库、确保默认管理员账号存在后启动 Textual TUI。

---

### `trade_relay/i18n.py` — 国际化

| 系统 | 语言 |
|------|------|
| Windows | 中文（`zh`） |
| Linux / macOS | 英文（`en`） |

通过 `platform.system()` 自动判断，提供 `t(key, *args)` 翻译函数，覆盖所有 UI 文本。

---

### `trade_relay/database.py` — 数据库层

基于 SQLite，包含三张表：

| 表名 | 用途 |
|------|------|
| `users` | 用户账号（id / username / password_hash / role / is_active） |
| `orders` | 订单记录（全用户共享视图） |
| `operation_logs` | 操作审计日志 |

所有写操作使用参数化查询，防止 SQL 注入。WAL 模式支持并发读写。

---

### `trade_relay/config.py` — 用户配置

每位用户在 `configs/users/{username}.yaml` 存放自己的 Binance 配置：

```yaml
binance:
  api_key: "..."
  api_secret: "..."
  testnet: false      # true 使用 Binance 测试网

trading:
  mock_mode: false    # true 则模拟下单，不调用真实 API
```

文件名经过安全过滤（仅允许字母数字/下划线/连字符），防止路径穿越攻击。

---

### `trade_relay/auth/manager.py` — 认证与权限

- **密码**：使用 `bcrypt`（cost factor 12）单向哈希存储
- **会话**：`Session` 对象，持有 `user_id / username / role`（进程内，不持久化）
- **默认管理员**：首次运行自动创建 `admin` / `Admin@123`（建议首次登录后立即修改）

#### 权限控制

| 操作 | 管理员 | 普通用户 |
|------|:------:|:--------:|
| 登录 | ✓ | ✓ |
| 下单 | ✓ | ✓ |
| 查看所有订单日志 | ✓ | ✓ |
| 创建用户 | ✓ | ✗ |
| 删除/停用用户 | ✓ | ✗ |
| 重置他人密码 | ✓ | ✗ |
| 修改自己的 API Key | ✓ | ✓ |

---

### `trade_relay/trading/binance_client.py` — Binance 封装

支持三种执行模式：

| 模式 | 配置 | 说明 |
|------|------|------|
| **Mock** | `mock_mode: true` | 本地模拟，生成假订单 ID，不联网 |
| **Testnet** | `testnet: true` | 连接 Binance 测试网，不消耗真实资产 |
| **Live** | 两者均为 `false` | 真实 Binance 主网下单 |

使用 `python-binance` 异步客户端，连接后自动关闭。

---

### `trade_relay/trading/order_manager.py` — 下单流程

```
submit_order(session, symbol, side, order_type, quantity, price)
    │
    ├── 参数校验（symbol / side / type / qty / price）
    ├── 读取用户配置（mock? testnet? api_key?）
    ├── 调用 binance_client（或 mock）
    ├── 写入 orders 表
    └── 写入 operation_logs 表
```

---

### `trade_relay/ui/` — 界面层

#### 屏幕路由（`app.py`）

```
TradeRelayApp
    └── LoginScreen          （dismiss 返回 Session）
            └── MainScreen   （收到 LogoutRequested 消息后返回登录）
```

#### 主界面标签页（`main_screen.py`）

| Tab | 权限 | 功能 |
|-----|------|------|
| 订单日志 | 所有用户 | 展示全用户订单，10 秒自动刷新 |
| 下单 | 所有用户 | 填写交易对/方向/类型/数量/价格，异步提交 |
| 用户管理 | **仅管理员** | 创建/停用用户，重置密码 |
| 设置 | 所有用户 | 配置个人 Binance API Key |

---

## 数据流

```
用户输入 → LoginScreen.dismiss(session)
         → MainScreen 获得 session
         → Place Order Tab 调用 submit_order(session, ...)
         → order_manager 写 DB + 调用 Binance API
         → Order Log Tab 从 DB 读取并展示
```

---

## 快速启动

所有运行环境均使用 **venv 虚拟环境**。

### Linux / macOS

```bash
# 一键初始化（创建 .venv + 安装依赖）
bash scripts/setup.sh

# 激活虚拟环境
source .venv/bin/activate

# 启动程序
python3 main.py
```

### Windows（开发模式）

```bat
REM 初始化虚拟环境并安装依赖
scripts\build_windows.bat

REM 或手动激活后运行
.venv\Scripts\activate
python main.py
```

默认管理员：`admin` / `Admin@123`

---

## Windows 打包

```bat
scripts\build_windows.bat
```

脚本会自动创建 `.venv`、安装依赖、调用 PyInstaller 打包。  
产物位于 `dist\TradeRelay\TradeRelay.exe`，控制台模式运行，支持 UTF-8 中文显示。

---

## 依赖

| 包 | 用途 |
|----|------|
| `textual>=0.52.0` | TUI 框架 |
| `bcrypt>=4.0.0` | 密码哈希 |
| `pyyaml>=6.0` | 用户配置文件读写 |
| `python-binance>=1.0.19` | Binance REST API（可选，mock 模式不需要） |
