# Trade Relay — 项目结构文档

> 当前架构为 Electron + React 前端，FastAPI + Python 交易后端。旧的 PyQt 界面层已移除。

---

## 目录结构

```
trade-relay/
├── main.py                          # Python 后端启动入口（uvicorn launcher）
├── requirements.txt                 # Python 运行依赖
├── pyproject.toml                   # Python 包元数据
├── package.json                     # Electron + React 工程配置
├── electron/                        # Electron 主进程与 preload
├── src/                             # React 前端
├── backend/                         # FastAPI 路由与服务启动
├── trade_relay/                     # Python 业务逻辑（DB / auth / exchange / trading）
├── configs/users/                   # 用户配置文件
├── scripts/setup.sh                 # Linux/macOS 初始化脚本
└── scripts/build_windows.bat        # Windows Electron 打包脚本
```

---

## 关键模块

### `main.py`

负责读取 `.env` 并启动 `backend.main:app`，用于本地开发或单独运行 Python 后端。

### `backend/`

FastAPI 应用入口、REST 路由、认证接口、订单接口、持仓接口都在这里。

### `src/` + `electron/`

Electron 承载 React 前端。前端通过 IPC 和本地 FastAPI 后端通信，展示登录、下单、持仓、委托、历史等界面。

### `trade_relay/`

保留纯 Python 业务逻辑：

- `database.py`：MySQL 数据访问与迁移
- `auth/`：用户认证与管理员初始化
- `exchange/`：Binance 交互与同步
- `trading/`：下单、状态流同步、订单持久化

---

## 启动方式

### Python 后端

```bash
source .venv/bin/activate
python3 main.py --reload
```

### Electron + React 前端

```bash
npm run dev
```

### Windows 打包

```bat
scripts\build_windows.bat
```

该脚本会安装 Python 和 Node.js 依赖，并执行 `npm run build:win` 生成 Electron Windows 安装包。
