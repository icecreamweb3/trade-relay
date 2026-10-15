'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const {
  applyQuickOrderPreference,
  buildQuickLimit,
  buildQuickStop,
  getLimitSide,
  getStopSide,
  normalizeQuickOrderPreference,
  resolveBaseQuantity,
  splitTradeSymbol,
} = require('../electron/chart-quick-orders')

test('limit shortcut is the passive counterpart to the stop shortcut', () => {
  assert.equal(getLimitSide(101, 100), 'SELL')
  assert.equal(getLimitSide(99, 100), 'BUY')
  assert.equal(getLimitSide(100, 100), null)
  assert.deepEqual(
    buildQuickLimit(99, 100, {
      username: 'will',
      quantity: 0.005,
      inputQuantity: 500,
      sizeUnit: 'QUOTE',
      leverage: 10,
      positionMode: 'DUAL',
    }, 'will'),
    {
      side: 'BUY',
      quantity: 0.005,
      inputQuantity: 500,
      sizeUnit: 'QUOTE',
      leverage: 10,
      positionMode: 'DUAL',
    },
  )
})

test('limit shortcut remains editable when no panel quantity is configured', () => {
  assert.deepEqual(buildQuickLimit(101, 100, null, 'will'), {
    side: 'SELL',
    quantity: null,
    inputQuantity: null,
    sizeUnit: 'QUOTE',
    leverage: 10,
    unavailableReason: 'order_quantity_required',
  })
})

test('price above current price offers Buy Stop only', () => {
  assert.equal(getStopSide(101, 100), 'BUY')
  assert.deepEqual(
    buildQuickStop(101, 100, {
      username: 'will',
      quantity: 0.005,
      inputQuantity: 500,
      sizeUnit: 'QUOTE',
      leverage: 10,
      positionMode: 'DUAL',
    }, 'will'),
    {
      side: 'BUY',
      quantity: 0.005,
      inputQuantity: 500,
      sizeUnit: 'QUOTE',
      leverage: 10,
      positionMode: 'DUAL',
    },
  )
})

test('price below current price offers Sell Stop only', () => {
  assert.equal(getStopSide(99, 100), 'SELL')
})

test('equal or invalid prices do not offer a stop order', () => {
  assert.equal(getStopSide(100, 100), null)
  assert.equal(getStopSide(0, 100), null)
})

test('menu remains available without a configured size so confirmation can collect one', () => {
  assert.deepEqual(buildQuickStop(101, 100, null, 'will'), {
    side: 'BUY',
    quantity: null,
    inputQuantity: null,
    sizeUnit: 'QUOTE',
    leverage: 10,
    unavailableReason: 'order_quantity_required',
  })
})

test('confirmation preserves the panel input and unit even before it forms a valid base size', () => {
  assert.deepEqual(
    buildQuickStop(101, 100, {
      username: 'will',
      quantity: null,
      inputQuantity: 1,
      sizeUnit: 'QUOTE',
      leverage: 20,
    }, 'will'),
    {
      side: 'BUY',
      quantity: null,
      inputQuantity: 1,
      sizeUnit: 'QUOTE',
      leverage: 20,
      unavailableReason: 'order_quantity_required',
    },
  )
})

test('settings from another authenticated user are rejected', () => {
  assert.equal(
    buildQuickStop(101, 100, { username: 'other', quantity: 0.005 }, 'will').quantity,
    null,
  )
})

test('edited quote or base quantities normalize to exchange base quantity', () => {
  assert.equal(resolveBaseQuantity(500, 'QUOTE', 83_000), 0.006)
  assert.equal(resolveBaseQuantity(0.0079, 'BASE', 83_000), 0.007)
  assert.equal(resolveBaseQuantity(1, 'QUOTE', 83_000), null)
})

test('symbol assets support BTCUSDC quantity-unit switching', () => {
  assert.deepEqual(splitTradeSymbol('BTCUSDC'), { baseAsset: 'BTC', quoteAsset: 'USDC' })
})

test('last successful chart quantity and unit override the panel size for the next shortcut', () => {
  assert.deepEqual(
    applyQuickOrderPreference(
      {
        username: 'will',
        quantity: 0.005,
        inputQuantity: 500,
        sizeUnit: 'QUOTE',
        leverage: 20,
        positionMode: 'DUAL',
      },
      { inputQuantity: 0.0079, sizeUnit: 'BASE' },
      'will',
      83_000,
    ),
    {
      username: 'will',
      quantity: 0.007,
      inputQuantity: 0.0079,
      sizeUnit: 'BASE',
      leverage: 20,
      positionMode: 'DUAL',
    },
  )
})

test('stored quote quantity is recalculated with the latest reference price', () => {
  const settings = applyQuickOrderPreference(
    null,
    { inputQuantity: 500, sizeUnit: 'QUOTE' },
    'will',
    100_000,
  )
  assert.deepEqual(settings, {
    username: 'will',
    quantity: 0.005,
    inputQuantity: 500,
    sizeUnit: 'QUOTE',
    leverage: 10,
  })
  assert.deepEqual(buildQuickStop(101, 100, settings, 'will'), {
    side: 'BUY',
    quantity: 0.005,
    inputQuantity: 500,
    sizeUnit: 'QUOTE',
    leverage: 10,
  })
})

test('invalid chart quantity preferences are ignored', () => {
  assert.equal(normalizeQuickOrderPreference(0, 'QUOTE'), null)
  assert.equal(normalizeQuickOrderPreference(10, 'INVALID'), null)
  const settings = { username: 'will', quantity: 0.01 }
  assert.equal(applyQuickOrderPreference(settings, null, 'will', 100), settings)
})
