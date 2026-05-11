export type Locale = 'zh-CN' | 'en'

export const translations: Record<Locale, Record<string, string>> = {
  'zh-CN': {
    // Login
    'login.title': 'Trade Relay',
    'login.subtitle': '多用户交易终端',
    'login.username': '用户名',
    'login.password': '密码',
    'login.submit': '登录',
    'login.loading': '登录中...',
    'login.error.required': '请输入用户名和密码',
    'login.error.failed': '用户名或密码错误',

    // TitleBar
    'title.app': 'Trade Relay',
    'status.connected': '数据流实时',
    'status.disconnected': '等待数据...',
    'nav.back': '后退',
    'nav.forward': '前进',
    'nav.reload': '刷新',
    'nav.chartExpand': '展开/还原 K 线图',

    // Screens
    'nav.trade': '交易',
    'nav.orders': '订单记录',
    'nav.users': '用户管理',
    'nav.profile': '我的收益',
    'nav.settings': '设置',
    'nav.logout': '退出',

    // Order form
    'order.symbol': '交易对',
    'order.side': '方向',
    'order.buy': '做多',
    'order.sell': '做空',
    'order.type': '类型',
    'order.limit': '限价',
    'order.market': '市价',
    'order.qty': '数量',
    'order.price': '价格',
    'order.margin.cross': '全仓',
    'order.margin.isolated': '逐仓',
    'order.open': '开仓',
    'order.close': '平仓',
    'order.tp': '止盈',
    'order.sl': '止损',
    'order.submit': '提交',
    'order.submitting': '提交中...',
    'order.success': '订单提交成功',

    // Positions
    'pos.title': '持仓',
    'pos.openOrders': '当前委托',
    'pos.history': '历史订单',
    'pos.tradeHistory': '成交历史',
    'pos.symbol': '合约',
    'pos.side': '方向',
    'pos.size': '数量',
    'pos.entry': '开仓均价',
    'pos.liq': '强平价',
    'pos.pnl': '未实现盈亏',
    'pos.leverage': '杠杆',
    'pos.margin': '保证金',
    'pos.long': '多',
    'pos.short': '空',
    'pos.empty': '暂无数据',
    'trade.commission': '手续费',

    // Order log
    'log.title': '订单记录',
    'log.time': '时间',
    'log.user': '用户',
    'log.symbol': '品种',
    'log.side': '方向',
    'log.type': '类型',
    'log.qty': '数量',
    'log.price': '价格',
    'log.status': '状态',
    'log.id': '订单号',

    // Status
    'status.filled': '已成交',
    'status.pending': '挂单中',
    'status.failed': '失败',
    'status.mock': '模拟',

    // Admin
    'admin.title': '用户管理',
    'admin.add': '新建用户',
    'admin.edit': '编辑',
    'admin.delete': '删除',
    'admin.activate': '启用',
    'admin.deactivate': '禁用',
    'admin.col.username': '用户名',
    'admin.col.role': '角色',
    'admin.col.active': '状态',
    'admin.col.created': '创建时间',

    // Config
    'config.title': 'Binance API 配置',
    'config.apiKey': 'API Key',
    'config.apiSecret': 'API Secret',
    'config.testnet': '测试网',
    'config.mockMode': '模拟交易',
    'config.save': '保存',

    // Profile
    'profile.title': '收益分析',
    'profile.totalPnl': '总盈亏',
    'profile.winRate': '胜率',
    'profile.trades': '总交易次数',
    'profile.commission': '总手续费',

    // Binance panel
    'binance.connected': 'Binance 数据流已连接',
    'binance.loading': '加载中...',
    'statusbar.live': '数据流实时',
    'statusbar.disconnected': '未连接',
    'statusbar.markPrice': '标记价',
    'statusbar.fundingRate': '资金费率',
  },
  'en': {
    'login.title': 'Trade Relay',
    'login.subtitle': 'Multi-user Trading Terminal',
    'login.username': 'Username',
    'login.password': 'Password',
    'login.submit': 'Login',
    'login.loading': 'Logging in...',
    'login.error.required': 'Please enter username and password',
    'login.error.failed': 'Invalid username or password',

    'title.app': 'Trade Relay',
    'status.connected': 'Live',
    'status.disconnected': 'Waiting...',
    'nav.back': 'Back',
    'nav.forward': 'Forward',
    'nav.reload': 'Reload',
    'nav.chartExpand': 'Expand/Restore Chart',

    'nav.trade': 'Trade',
    'nav.orders': 'Orders',
    'nav.users': 'Users',
    'nav.profile': 'Profile',
    'nav.settings': 'Settings',
    'nav.logout': 'Logout',

    'order.symbol': 'Symbol',
    'order.side': 'Side',
    'order.buy': 'Long',
    'order.sell': 'Short',
    'order.type': 'Type',
    'order.limit': 'Limit',
    'order.market': 'Market',
    'order.qty': 'Quantity',
    'order.price': 'Price',
    'order.margin.cross': 'Cross',
    'order.margin.isolated': 'Isolated',
    'order.open': 'Open',
    'order.close': 'Close',
    'order.tp': 'Take Profit',
    'order.sl': 'Stop Loss',
    'order.submit': 'Submit',
    'order.submitting': 'Submitting...',
    'order.success': 'Order submitted',

    'pos.title': 'Positions',
    'pos.openOrders': 'Open Orders',
    'pos.history': 'Order History',
    'pos.tradeHistory': 'Trade History',
    'pos.symbol': 'Contract',
    'pos.side': 'Side',
    'pos.size': 'Size',
    'pos.entry': 'Entry Price',
    'pos.liq': 'Liq. Price',
    'pos.pnl': 'Unrealized PnL',
    'pos.leverage': 'Leverage',
    'pos.margin': 'Margin',
    'pos.long': 'LONG',
    'pos.short': 'SHORT',
    'pos.empty': 'No data',
    'trade.commission': 'Commission',

    'log.title': 'Order Log',
    'log.time': 'Time',
    'log.user': 'User',
    'log.symbol': 'Symbol',
    'log.side': 'Side',
    'log.type': 'Type',
    'log.qty': 'Qty',
    'log.price': 'Price',
    'log.status': 'Status',
    'log.id': 'Order ID',

    'status.filled': 'FILLED',
    'status.pending': 'PENDING',
    'status.failed': 'FAILED',
    'status.mock': 'MOCK',

    'admin.title': 'User Management',
    'admin.add': 'Add User',
    'admin.edit': 'Edit',
    'admin.delete': 'Delete',
    'admin.activate': 'Activate',
    'admin.deactivate': 'Deactivate',
    'admin.col.username': 'Username',
    'admin.col.role': 'Role',
    'admin.col.active': 'Status',
    'admin.col.created': 'Created',

    'config.title': 'Binance API Config',
    'config.apiKey': 'API Key',
    'config.apiSecret': 'API Secret',
    'config.testnet': 'Testnet',
    'config.mockMode': 'Mock Mode',
    'config.save': 'Save',

    'profile.title': 'P&L Analysis',
    'profile.totalPnl': 'Total P&L',
    'profile.winRate': 'Win Rate',
    'profile.trades': 'Total Trades',
    'profile.commission': 'Total Commission',

    'binance.connected': 'Binance data stream connected',
    'binance.loading': 'Loading...',
    'statusbar.live': 'Live',
    'statusbar.disconnected': 'Disconnected',
    'statusbar.markPrice': 'Mark',
    'statusbar.fundingRate': 'Funding',
  },
}

export function useTranslation(locale: Locale) {
  return {
    t: (key: string, vars?: Record<string, string | number>): string => {
      const dict = translations[locale] ?? translations['en']
      let str = dict[key] ?? translations['en'][key] ?? key
      if (vars) {
        str = str.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ''))
      }
      return str
    },
  }
}
