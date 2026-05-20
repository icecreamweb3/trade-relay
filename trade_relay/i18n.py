"""
Internationalization support.

Default locale:
  Windows  → zh
  Linux    → en

Override via (highest priority first):
  1. set_locale("zh" | "en")  – called from main.py --lang argument
  2. Environment variable  TRADE_RELAY_LANG=zh | en
  3. Platform default
"""
import os
import platform

TRANSLATIONS = {
    "en": {
        # App
        "app_title": "Trade Relay - Multi-User Trading Terminal",
        "app_subtitle": "Powered by Binance",
        # Auth
        "login": "Login",
        "logout": "Logout",
        "username": "Username",
        "password": "Password",
        "confirm_password": "Confirm Password",
        "login_btn": "Login",
        "login_success": "Login successful. Welcome, {}!",
        "login_failed": "Login failed: Invalid username or password",
        "logout_success": "Logged out successfully",
        "not_logged_in": "Please login first",
        # Navigation
        "view": "View",
        "order_log": "Order Log",
        "place_order": "Trade",
        "user_management": "User Management",
        "settings": "Settings",
        "refresh": "Refresh",
        # Order form
        "symbol": "Symbol",
        "side": "Side",
        "buy": "BUY",
        "sell": "SELL",
        "order_type": "Order Type",
        "market": "MARKET",
        "limit": "LIMIT",
        "quantity": "Quantity",
        "stop_price": "Trigger Price",
        "price": "Price (LIMIT only)",
        "submit_order": "Submit Order",
        "cancel": "Cancel",
        "order_success": "Order placed successfully! ID: {}",
        "order_mock": "[MOCK] Order simulated: {} {} {} qty={}",
        "order_failed": "Order failed: {}",
        "no_api_key": "No Binance API key configured. Please set up in Settings.",
        # Order log
        "col_time": "Time",
        "col_user": "User",
        "col_symbol": "Symbol",
        "col_side": "Side",
        "col_type": "Type",
        "col_qty": "Quantity",
        "col_price": "Price",
        "col_status": "Status",
        "col_order_id": "Order ID",
        "no_orders": "No orders yet",
        "status_filled": "FILLED",
        "status_pending": "PENDING",
        "status_failed": "FAILED",
        "status_mock": "MOCK",
        # Admin
        "create_user": "Create User",
        "add_user": "Add User",
        "edit_user": "Edit User",
        "delete_user": "Delete User",
        "reset_password": "Reset Password",
        "new_password": "New Password",
        "new_password_optional": "New Password (blank = unchanged)",
        "role": "Role",
        "role_admin": "Admin",
        "role_user": "User",
        "user_created": "User '{}' created successfully",
        "user_deleted": "User '{}' deleted",
        "user_exists": "Username '{}' already exists",
        "user_updated": "User '{}' updated successfully",
        "cannot_delete_self": "Cannot delete your own account",
        "cannot_delete_admin": "Cannot delete an Admin account",
        "password_reset": "Password reset for '{}'",
        "col_username": "Username",
        "col_role": "Role",
        "col_created": "Created",
        "col_active": "Active",
        "confirm_delete_users": "Delete {} selected user(s)? This action cannot be undone.",
        "delete_success": "{} user(s) deleted successfully",
        "delete_partial": "{} deletion(s) failed",
        "activate_user": "Activate",
        "freeze_user": "Deactivate",
        "user_activated": "User '{}' activated",
        "user_frozen": "User '{}' deactivated",
        "col_api_key": "API Key",
        "col_api_secret": "API Secret",
        "col_updated": "Updated",
        # Profile
        "profile": "Profile",
        "equity_curve": "Equity Curve (Cumulative P&L)",
        "daily_pnl": "Daily P&L",
        "select_user": "Select User",
        "total_pnl": "Total P&L",
        "win_rate": "Win Rate",
        "total_trades": "Total Trades",
        "total_commission": "Total Commission",
        "no_data": "No trade data available",
        "refresh": "Refresh",
        # Settings
        "api_key": "Binance API Key",
        "api_secret": "Binance API Secret",
        "testnet": "Use Testnet",
        "mock_mode": "Mock Mode (no real orders)",
        "save_settings": "Save Settings",
        "settings_saved": "Settings saved successfully",
        "settings_error": "Error saving settings: {}",
        # General
        "error": "Error",
        "success": "Success",
        "confirm": "Confirm",
        "warning": "Warning",
        "current_user": "User: {}",
        "role_badge_admin": "[ADMIN]",
        "role_badge_user": "[USER]",
        "field_required": "Field '{}' is required",
        "passwords_mismatch": "Passwords do not match",
        "unauthorized": "You do not have permission to perform this action",
        "yes": "Yes",
        "no": "No",
        # Order book panel
        "order_book": "Order Book",
        "ob_price_col": "Price (USDC)",
        "ob_qty_col": "Qty (USDC)",
        "ob_total_col": "Total (USDC)",
        # Trading form panel
        "margin_cross": "Cross",
        "open_position": "Open",
        "close_position": "Close",
        "limit_tab": "Limit",
        "market_tab": "Market",
        "conditional_tab": "Conditional",
        "available_balance": "Available",
        "order_price_label": "Price",
        "order_qty_label": "Quantity",
        "take_profit_stop_loss": "TP/SL",
        "tpsl_advanced":          "Advanced",
        "tpsl_tp":                "Take Profit",
        "tpsl_sl":                "Stop Loss",
        "tpsl_latest":            "Latest",
        "tpsl_order_price":       "Order Price",
        "trigger_price_label": "Trigger Price",
        "time_in_force": "Time in Force",
        "go_long": "Buy / Long",
        "go_short": "Sell / Short",
        "liquidation_price": "Liq. Price",
        "margin_label": "Margin",
        "available_to_open": "Avail. Open",
        "refresh_book_tip": "Refresh order book",
        "market_placeholder": "Market Price",
        "ticker_default_msg": "  ★  Wishing all traders a profitable day today!  ",
        "sync_tickers": "Sync All Tickers",        "reload_page":   "Refresh Page",        "sync_tickers_running": "Syncing tickers from Binance...",
        "sync_tickers_ok": "Tickers synced: {} symbols updated.",
        "sync_tickers_fail": "Ticker sync failed: {}",
        # Positions panel tabs
        "tab_positions":     "Positions",
        "tab_open_orders":   "Open Orders",
        "tab_order_history": "Order History",
        "tab_trade_history": "Trade History",
        # Position table columns
        "pos_contract":    "Contract",
        "pos_side":        "Side",
        "pos_size":        "Size",
        "pos_entry_price": "Entry Price",
        "pos_liq_price":   "Liq. Price",
        "pos_pnl":         "Unrealized PnL",
        "pos_leverage":    "Leverage",
        "pos_margin_type": "Margin",
        "pos_margin":      "Margin(USDC)",
        # Trade history columns
        "col_filled_qty":  "Filled Qty",
        "col_avg_price":   "Avg Price",
        # Recent trades panel (platform trades)
        "recent_trades":   "Recent Trades",
        "rt_user_col":     "User",
        "rt_symbol_col":   "Symbol",
        "rt_side_col":     "Side",
        "rt_qty_col":      "Quantity",
        "rt_value_col":    "Value",
        "rt_time_col":     "Time",
        # Account info panel
        "acct_equity":          "Equity",
        "acct_margin_used":     "Used Margin",
        "acct_available":       "Available",
        "acct_pnl":             "Unrealized PnL",
        "acct_title":           "Account",
        "acct_margin_ratio":    "Margin Ratio",
        "acct_risk_rate":       "Risk Rate",
        "acct_maint_margin":    "Maint. Margin",
        "acct_total_equity":    "Total Equity",
        "acct_pos_value":       "Position Value",
        "acct_actual_leverage": "Actual Leverage",
        "acct_combined_margin": "Combined Margin",
        "acct_wallet_balance":  "Wallet Balance",
        "position_mode_invalid": "Position mode must be SINGLE or DUAL",
        "position_mode_switch_blocked": (
            "Cannot switch position mode while open positions or orders exist. "
            "Close all positions and cancel all open orders first. "
            "positions={} open_orders={} open_algo_orders={}"
        ),
        "position_mode_switch_failed": "Failed to set position mode to {}",
    },
    "zh": {
        # App
        "app_title": "交易中继 - 多用户交易终端",
        "app_subtitle": "由 Binance 驱动",
        # Auth
        "login": "登录",
        "logout": "退出登录",
        "username": "用户名",
        "password": "密码",
        "confirm_password": "确认密码",
        "login_btn": "登录",
        "login_success": "登录成功，欢迎 {}！",
        "login_failed": "登录失败：用户名或密码错误",
        "logout_success": "已成功退出登录",
        "not_logged_in": "请先登录",
        # Navigation
        "view": "视图",
        "order_log": "订单日志",
        "place_order": "交易",
        "user_management": "用户管理",
        "settings": "设置",
        "refresh": "刷新",
        # Order form
        "symbol": "交易对",
        "side": "方向",
        "buy": "买入",
        "sell": "卖出",
        "order_type": "订单类型",
        "market": "市价单",
        "limit": "限价单",
        "quantity": "数量",
        "stop_price": "触发价格",
        "price": "价格（限价单）",
        "submit_order": "提交订单",
        "cancel": "取消",
        "order_success": "下单成功！订单ID: {}",
        "order_mock": "[模拟] 模拟下单: {} {} {} 数量={}",
        "order_failed": "下单失败：{}",
        "no_api_key": "未配置 Binance API 密钥，请在设置中配置。",
        # Order log
        "col_time": "时间",
        "col_user": "用户",
        "col_symbol": "交易对",
        "col_side": "方向",
        "col_type": "类型",
        "col_qty": "数量",
        "col_price": "价格",
        "col_status": "状态",
        "col_order_id": "订单ID",
        "no_orders": "暂无订单",
        "status_filled": "已成交",
        "status_pending": "等待中",
        "status_failed": "失败",
        "status_mock": "模拟",
        # Admin
        "create_user": "创建用户",
        "add_user": "新增",
        "edit_user": "修改",
        "delete_user": "删除用户",
        "reset_password": "重置密码",
        "new_password": "新密码",
        "new_password_optional": "新密码（留空则不修改）",
        "role": "角色",
        "role_admin": "管理员",
        "role_user": "普通用户",
        "user_created": "用户 '{}' 创建成功",
        "user_deleted": "用户 '{}' 已删除",
        "user_exists": "用户名 '{}' 已存在",
        "user_updated": "用户 '{}' 更新成功",
        "cannot_delete_self": "不能删除自己的账户",
        "cannot_delete_admin": "不能删除管理员账户",
        "password_reset": "已重置 '{}' 的密码",
        "col_username": "用户名",
        "col_role": "角色",
        "col_created": "创建时间",
        "col_active": "状态",
        "confirm_delete_users": "确认删除选中的 {} 个用户？此操作不可撤销。",
        "delete_success": "已成功删除 {} 个用户",
        "delete_partial": "{} 个用户删除失败",
        "activate_user": "激活",
        "freeze_user": "禁用",
        "user_activated": "用户 '{}' 已激活",
        "user_frozen": "用户 '{}' 已禁用",
        "col_api_key": "API Key",
        "col_api_secret": "API Secret",
        "col_updated": "更新时间",
        # Profile
        "profile": "Profile",
        "equity_curve": "资金曲线（累计盈亏）",
        "daily_pnl": "每日盈亏",
        "select_user": "选择用户",
        "total_pnl": "总盈亏",
        "win_rate": "胜率",
        "total_trades": "总交易次数",
        "total_commission": "总手续费",
        "no_data": "暂无交易数据",
        "refresh": "刷新",
        # Settings
        "api_key": "Binance API Key",
        "api_secret": "Binance API Secret",
        "testnet": "使用测试网",
        "mock_mode": "模拟模式（不发送真实订单）",
        "save_settings": "保存设置",
        "settings_saved": "设置保存成功",
        "settings_error": "保存设置出错：{}",
        # General
        "error": "错误",
        "success": "成功",
        "confirm": "确认",
        "warning": "警告",
        "current_user": "用户：{}",
        "role_badge_admin": "【管理员】",
        "role_badge_user": "【普通用户】",
        "field_required": "字段 '{}' 不能为空",
        "passwords_mismatch": "两次输入的密码不一致",
        "unauthorized": "您没有权限执行此操作",
        "yes": "是",
        "no": "否",
        # Order book panel
        "order_book": "订单簿",
        "ob_price_col": "价格 (USDC)",
        "ob_qty_col": "数量 (USDC)",
        "ob_total_col": "合计 (USDC)",
        # Trading form panel
        "margin_cross": "全仓",
        "open_position": "开仓",
        "close_position": "平仓",
        "limit_tab": "限价",
        "market_tab": "市价",
        "conditional_tab": "条件委托",
        "available_balance": "可用",
        "order_price_label": "委托价格",
        "order_qty_label": "数量",
        "take_profit_stop_loss": "止盈/止损",
        "tpsl_advanced":          "高级",
        "tpsl_tp":                "止盈",
        "tpsl_sl":                "止损",
        "tpsl_latest":            "最新",
        "tpsl_order_price":       "委托价格",
        "trigger_price_label": "触发价格",
        "time_in_force": "生效时间",
        "go_long": "开多",
        "go_short": "开空",
        "liquidation_price": "强平价格",
        "margin_label": "保证金",
        "available_to_open": "可开",
        "refresh_book_tip": "刷新订单簿",
        "market_placeholder": "市价",
        "ticker_default_msg": "  ★  祝各位交易员，今日大赚！！！  ",
        "sync_tickers": "同步所有交易对",
        "reload_page":  "刷新页面",
        "sync_tickers_running": "正在从 Binance 同步交易对...",
        "sync_tickers_ok": "交易对同步完成：已更新 {} 个交易对。",
        "sync_tickers_fail": "交易对同步失败：{}",
        # Positions panel tabs
        "tab_positions":     "仓位",
        "tab_open_orders":   "当前委托",
        "tab_order_history": "历史委托",
        "tab_trade_history": "历史成交",
        # Position table columns
        "pos_contract":    "合约",
        "pos_side":        "持仓方向",
        "pos_size":        "数量",
        "pos_entry_price": "开仓均价",
        "pos_liq_price":   "强平价",
        "pos_pnl":         "未实现盈亏",
        "pos_leverage":    "杠杆",
        "pos_margin_type": "仓位模式",
        "pos_margin":      "保证金(USDC)",
        # Trade history columns
        "col_filled_qty":  "成交数量",
        "col_avg_price":   "成交均价",
        # Recent trades panel (platform trades)
        "recent_trades":   "最新成交",
        "rt_user_col":     "用户",
        "rt_symbol_col":   "交易对",
        "rt_side_col":     "方向",
        "rt_qty_col":      "数量",
        "rt_value_col":    "成交额",
        "rt_time_col":     "时间",
        # Account info panel
        "acct_equity":          "权益",
        "acct_margin_used":     "已用保证金",
        "acct_available":       "可用余额",
        "acct_pnl":             "未实现盈亏",
        "acct_title":           "账户",
        "acct_margin_ratio":    "保证金比率",
        "acct_risk_rate":       "账户风险率",
        "acct_maint_margin":    "账户维持保证金",
        "acct_total_equity":    "账户总权益",
        "acct_pos_value":       "仓位估值",
        "acct_actual_leverage": "实际杠杆",
        "acct_combined_margin": "联合保证金",
        "acct_wallet_balance":  "钉包余额",
        "position_mode_invalid": "持仓模式必须是 SINGLE 或 DUAL",
        "position_mode_switch_blocked": (
            "当前存在持仓或委托，无法切换持仓模式。"
            "请先平掉全部持仓并取消全部挂单。"
            "positions={} open_orders={} open_algo_orders={}"
        ),
        "position_mode_switch_failed": "切换持仓模式到 {} 失败",
    },
}


def get_locale() -> str:
    """
    Determine locale (lowest-priority fallback).
      Windows  → zh
      Linux    → en
    Can be overridden by TRADE_RELAY_LANG env var or set_locale().
    """
    env = os.environ.get("TRADE_RELAY_LANG", "").strip().lower()
    if env in ("zh", "en"):
        return env
    if platform.system() == "Windows":
        return "zh"
    return "en"


_locale: str = get_locale()
_translations: dict = TRANSLATIONS.get(_locale, TRANSLATIONS["en"])


def set_locale(lang: str) -> None:
    """
    Override the active locale at runtime.
    Call this before building any UI widgets.
    Accepted values: 'zh', 'en'  (case-insensitive).
    """
    global _locale, _translations
    lang = lang.strip().lower()
    if lang not in TRANSLATIONS:
        raise ValueError(f"Unsupported locale '{lang}'. Choose from: {list(TRANSLATIONS)}.")
    _locale = lang
    _translations = TRANSLATIONS[lang]


def current_locale() -> str:
    """Return the currently active locale code."""
    return _locale


def t(key: str, *args) -> str:
    """Translate key with optional format arguments."""
    text = _translations.get(key, TRANSLATIONS["en"].get(key, key))
    if args:
        return text.format(*args)
    return text
