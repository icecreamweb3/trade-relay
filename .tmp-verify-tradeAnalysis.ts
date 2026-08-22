/* 临时验证脚本：用 simba 08/17–08/22 全部 46 笔已成交订单验证修复后的配对逻辑 */
import { computeTradeAnalysis, OrderLike } from './src/utils/tradeAnalysis'

// (id, 成交时间, side, 开平, qty, avg_price, realized_pnl, fee)
const R: Array<[number, string, string, string, number, number, number | null, number]> = [
  [46, '2026-08-17 18:27:39', 'SELL', 'OPEN', 0.01, 63448.60, null, 0.0],
  [45, '2026-08-18 15:10:20', 'BUY', 'OPEN', 0.01, 64255.40, null, 0.0],
  [44, '2026-08-18 15:30:08', 'SELL', 'CLOSE', 0.01, 64178.60, -0.7680, 0.2567],
  [43, '2026-08-18 16:32:56', 'BUY', 'OPEN', 0.01, 64169.30, null, 0.0],
  [42, '2026-08-18 17:04:25', 'SELL', 'CLOSE', 0.01, 64160.80, -0.0850, 0.2566],
  [41, '2026-08-18 21:38:53', 'BUY', 'OPEN', 0.01, 64081.90, null, 0.2563],
  [40, '2026-08-18 22:00:32', 'SELL', 'CLOSE', 0.01, 64148.40, 0.6650, 0.2566],
  [39, '2026-08-19 10:32:03', 'BUY', 'OPEN', 0.01, 64316.70, null, 0.0],
  [38, '2026-08-19 11:01:34', 'SELL', 'CLOSE', 0.01, 64262.48, -0.5422, 0.2570],
  [37, '2026-08-19 11:47:01', 'BUY', 'OPEN', 0.01, 64283.40, null, 0.0],
  [36, '2026-08-19 13:53:22', 'SELL', 'CLOSE', 0.01, 64183.90, -0.9950, 0.2567],
  [35, '2026-08-19 17:05:06', 'BUY', 'OPEN', 0.01, 64397.60, null, 0.0],
  [34, '2026-08-19 17:09:27', 'SELL', 'CLOSE', 0.01, 64325.80, -0.7180, 0.2573],
  [33, '2026-08-19 21:36:39', 'SELL', 'OPEN', 0.01, 64868.10, null, 0.2595],
  [32, '2026-08-19 22:13:31', 'BUY', 'CLOSE', 0.01, 65270.60, -4.0250, 0.2611],
  [31, '2026-08-20 14:36:20', 'BUY', 'OPEN', 0.01, 69630.90, null, 0.2785],
  [30, '2026-08-20 15:30:58', 'SELL', 'CLOSE', 0.01, 69515.20, -1.1570, 0.2781],
  [29, '2026-08-20 16:10:48', 'BUY', 'OPEN', 0.01, 70781.70, null, 0.2831],
  [28, '2026-08-20 16:18:53', 'SELL', 'CLOSE', 0.01, 71259.60, 4.7790, 0.2850],
  [27, '2026-08-20 16:32:39', 'BUY', 'OPEN', 0.01, 71343.70, null, 0.0],
  [26, '2026-08-20 16:42:39', 'SELL', 'CLOSE', 0.01, 71381.00, 0.3730, 0.2855],
  [25, '2026-08-20 21:45:08', 'BUY', 'OPEN', 0.01, 71615.40, null, 0.2865],
  [24, '2026-08-20 22:19:12', 'SELL', 'CLOSE', 0.01, 71573.30, -0.4210, 0.2863],
  [23, '2026-08-20 22:56:05', 'BUY', 'OPEN', 0.01, 71628.10, null, 0.0],
  [22, '2026-08-20 23:08:12', 'SELL', 'CLOSE', 0.01, 72050.00, 4.2190, 0.2882],
  [21, '2026-08-20 23:11:56', 'BUY', 'OPEN', 0.01, 72181.00, null, 0.2887],
  [20, '2026-08-20 23:24:07', 'SELL', 'CLOSE', 0.01, 71915.70, -2.6530, 0.2877],
  [19, '2026-08-21 10:32:38', 'BUY', 'OPEN', 0.01, 74988.30, null, 0.3000],
  [18, '2026-08-21 10:33:38', 'SELL', 'CLOSE', 0.01, 74530.40, -4.5790, 0.2981],
  [17, '2026-08-21 16:12:55', 'SELL', 'OPEN', 0.01, 76811.90, null, 0.0],
  [16, '2026-08-21 16:14:03', 'BUY', 'OPEN', 0.01, 77060.80, null, 0.3082],
  [15, '2026-08-21 16:26:36', 'BUY', 'CLOSE', 0.01, 77217.10, -4.0520, 0.3089],
  [14, '2026-08-21 16:47:11', 'SELL', 'CLOSE', 0.01, 77867.20, 8.0640, 0.3115],
  [13, '2026-08-21 17:46:03', 'BUY', 'OPEN', 0.01, 78261.30, null, 0.3130],
  [12, '2026-08-21 17:58:46', 'SELL', 'CLOSE', 0.01, 77737.90, -5.2340, 0.3110],
  [11, '2026-08-21 19:02:27', 'BUY', 'OPEN', 0.005, 77627.10, null, 0.0],
  [10, '2026-08-21 19:12:42', 'SELL', 'CLOSE', 0.005, 77340.70, -1.4320, 0.1547],
  [9, '2026-08-21 21:36:37', 'SELL', 'OPEN', 0.005, 76452.50, null, 0.0],
  [8, '2026-08-21 21:40:46', 'BUY', 'CLOSE', 0.005, 77042.00, -2.9475, 0.1541],
  [7, '2026-08-21 21:44:54', 'BUY', 'OPEN', 0.005, 77268.00, null, 0.1545],
  [6, '2026-08-21 22:07:21', 'SELL', 'CLOSE', 0.005, 76961.70, -1.5315, 0.1539],
  [5, '2026-08-21 23:15:39', 'BUY', 'OPEN', 0.005, 77758.20, null, 0.0],
  [4, '2026-08-21 23:21:19', 'SELL', 'CLOSE', 0.005, 77512.10, -1.2305, 0.1550],
  [3, '2026-08-22 10:35:08', 'SELL', 'OPEN', 0.01, 78200.80, null, 0.3128],
  [2, '2026-08-22 10:45:06', 'BUY', 'CLOSE', 0.01, 78481.30, -2.8050, 0.3139],
  [1, '2026-08-22 14:00:52', 'SELL', 'OPEN', 0.005, 77388.70, null, 0.0],
]

const orders: OrderLike[] = R.map(([id, t, side, dir, qty, px, pnl, fee]) => ({
  id,
  username: 'simba',
  symbol: 'BTCUSDC',
  side,
  trade_direction: dir,
  filled_qty: qty,
  avg_price: px,
  realized_pnl: pnl,
  commission: fee,
  commission_asset: 'USDC',
  updated_at: t,
}))

const a = computeTradeAnalysis(orders)
console.log('fillCount   =', a.fillCount)
console.log('tradeCount  =', a.tradeCount)
console.log('win/loss    =', a.winCount, '/', a.lossCount)
console.log('winRate     =', a.winRate == null ? null : (a.winRate * 100).toFixed(1) + '%')
console.log('totalPnl    =', a.totalPnl.toFixed(4))
console.log('maxProfit   =', a.maxProfitTrip?.pnl.toFixed(4), 'entry:', a.maxProfitTrip?.entryTimes, 'exit:', a.maxProfitTrip?.exitTimes)
console.log('maxLoss     =', a.maxLossTrip?.pnl.toFixed(4))
console.log('commissions =', JSON.stringify(a.commissions))

const expect = { tradeCount: 22, win: 5, loss: 17, totalPnl: -17.0757, maxProfit: 8.064, maxLoss: -5.234 }
const ok =
  a.tradeCount === expect.tradeCount &&
  a.winCount === expect.win &&
  a.lossCount === expect.loss &&
  Math.abs(a.totalPnl - expect.totalPnl) < 1e-6 &&
  Math.abs((a.maxProfitTrip?.pnl ?? 0) - expect.maxProfit) < 1e-9 &&
  Math.abs((a.maxLossTrip?.pnl ?? 0) - expect.maxLoss) < 1e-9
console.log(ok ? 'PASS: 与人工核算的正确结果一致' : 'FAIL: 与预期不符')
process.exit(ok ? 0 : 1)
