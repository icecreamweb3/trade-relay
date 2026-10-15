import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const sourceUrl = new URL('../src/utils/chartActiveOrders.ts', import.meta.url)
const source = await readFile(sourceUrl, 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2020, target: ts.ScriptTarget.ES2020 },
}).outputText
const { selectChartActiveOrders } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`
)

function order(overrides = {}) {
  return {
    id: 1,
    symbol: 'BTCUSDC',
    exchange_order_id: '10001',
    side: 'SELL',
    order_type: 'LIMIT',
    trade_direction: 'CLOSE',
    reduce_only: true,
    status: 'NEW',
    price: 82_000,
    quantity: 0.005,
    filled_qty: 0,
    ...overrides,
  }
}

test('includes an order-panel close LIMIT as a chart take-profit line', () => {
  assert.deepEqual(selectChartActiveOrders([order()], 'btcusdc'), [{
    id: 1,
    symbol: 'BTCUSDC',
    exchange_order_id: '10001',
    side: 'SELL',
    order_type: 'LIMIT',
    trade_direction: 'CLOSE',
    reduce_only: true,
    price: 82_000,
    quantity: 0.005,
    remaining_quantity: 0.005,
    cancel_kind: 'basic',
  }])
})

test('includes the unfilled remainder of a partially-filled take-profit limit', () => {
  const [line] = selectChartActiveOrders([
    order({ order_type: 'TAKE_PROFIT', status: 'PARTIALLY_FILLED', filled_qty: 0.002 }),
  ], 'BTCUSDC')

  assert.equal(line.remaining_quantity, 0.003)
})

test('excludes filled, market, invalid-price, and other-symbol orders', () => {
  const orders = [
    order({ status: 'FILLED' }),
    order({ order_type: 'MARKET' }),
    order({ price: null }),
    order({ symbol: 'ETHUSDC' }),
  ]

  assert.deepEqual(selectChartActiveOrders(orders, 'BTCUSDC'), [])
})

test('includes active Buy Stop and Sell Stop conditionals at their trigger prices', () => {
  const conditionalOrders = [
    {
      algo_id: 20001,
      symbol: 'BTCUSDC',
      side: 'BUY',
      order_type: 'STOP_MARKET',
      quantity: 0.004,
      trigger_price: 83_000,
      status: 'NEW',
      trade_direction: 'OPEN',
    },
    {
      algo_id: 20002,
      symbol: 'BTCUSDC',
      side: 'SELL',
      order_type: 'STOP_MARKET',
      quantity: 0.003,
      trigger_price: 79_000,
      status: 'WORKING',
      trade_direction: 'OPEN',
    },
  ]

  assert.deepEqual(selectChartActiveOrders([], 'BTCUSDC', conditionalOrders), [
    {
      id: 20001,
      algo_id: 20001,
      symbol: 'BTCUSDC',
      side: 'BUY',
      order_type: 'STOP_MARKET',
      trade_direction: 'OPEN',
      reduce_only: false,
      price: 83_000,
      quantity: 0.004,
      remaining_quantity: 0.004,
      cancel_kind: 'conditional',
    },
    {
      id: 20002,
      algo_id: 20002,
      symbol: 'BTCUSDC',
      side: 'SELL',
      order_type: 'STOP_MARKET',
      trade_direction: 'OPEN',
      reduce_only: false,
      price: 79_000,
      quantity: 0.003,
      remaining_quantity: 0.003,
      cancel_kind: 'conditional',
    },
  ])
})

test('excludes inactive and non-stop conditional orders', () => {
  const conditionalOrders = [
    { algo_id: 1, symbol: 'BTCUSDC', side: 'BUY', order_type: 'STOP_MARKET', quantity: 1, trigger_price: 83_000, status: 'FILLED' },
    { algo_id: 2, symbol: 'BTCUSDC', side: 'SELL', order_type: 'TAKE_PROFIT_MARKET', quantity: 1, trigger_price: 79_000, status: 'NEW' },
    { algo_id: 3, symbol: 'ETHUSDC', side: 'BUY', order_type: 'STOP_MARKET', quantity: 1, trigger_price: 3_000, status: 'NEW' },
  ]

  assert.deepEqual(selectChartActiveOrders([], 'BTCUSDC', conditionalOrders), [])
})
