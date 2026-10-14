'use strict'

const QUOTE_ASSETS = ['USDT', 'USDC', 'FDUSD', 'BUSD', 'BTC', 'ETH']

function splitTradeSymbol(symbol) {
  const normalized = String(symbol || '').trim().toUpperCase()
  const quoteAsset = QUOTE_ASSETS.find((asset) => (
    normalized.endsWith(asset) && normalized.length > asset.length
  ))
  return quoteAsset
    ? { baseAsset: normalized.slice(0, -quoteAsset.length), quoteAsset }
    : { baseAsset: normalized, quoteAsset: 'QUOTE' }
}

function resolveBaseQuantity(inputQuantity, sizeUnit, currentPrice) {
  const input = Number(inputQuantity)
  const current = Number(currentPrice)
  if (!Number.isFinite(input) || input <= 0) return null
  const unrounded = sizeUnit === 'BASE'
    ? input
    : Number.isFinite(current) && current > 0
      ? input / current
      : NaN
  if (!Number.isFinite(unrounded) || unrounded <= 0) return null
  const quantity = Math.floor(unrounded * 1000) / 1000
  return quantity > 0 ? quantity : null
}

function getStopSide(triggerPrice, currentPrice) {
  const trigger = Number(triggerPrice)
  const current = Number(currentPrice)
  if (!Number.isFinite(trigger) || trigger <= 0 || !Number.isFinite(current) || current <= 0) return null
  if (trigger === current) return null
  return trigger > current ? 'BUY' : 'SELL'
}

function getLimitSide(limitPrice, currentPrice) {
  const stopSide = getStopSide(limitPrice, currentPrice)
  if (!stopSide) return null
  return stopSide === 'BUY' ? 'SELL' : 'BUY'
}

function buildQuickOrder(side, settings, username) {
  if (!side) return null
  const settingsAreCurrent = settings
    && settings.username === String(username || '')
    && Number(settings.quantity) > 0
  if (!settingsAreCurrent) {
    const belongsToUser = settings?.username === String(username || '')
    return {
      side,
      quantity: null,
      inputQuantity: belongsToUser && Number(settings.inputQuantity) > 0
        ? Number(settings.inputQuantity)
        : null,
      sizeUnit: belongsToUser && settings.sizeUnit === 'BASE' ? 'BASE' : 'QUOTE',
      leverage: belongsToUser ? settings.leverage : 10,
      ...(belongsToUser && settings.positionMode ? { positionMode: settings.positionMode } : {}),
      unavailableReason: 'order_quantity_required',
    }
  }
  return {
    side,
    quantity: Number(settings.quantity),
    inputQuantity: Number(settings.inputQuantity),
    sizeUnit: settings.sizeUnit,
    leverage: settings.leverage,
    positionMode: settings.positionMode,
  }
}

function buildQuickStop(triggerPrice, currentPrice, settings, username) {
  return buildQuickOrder(getStopSide(triggerPrice, currentPrice), settings, username)
}

function buildQuickLimit(limitPrice, currentPrice, settings, username) {
  return buildQuickOrder(getLimitSide(limitPrice, currentPrice), settings, username)
}

module.exports = {
  buildQuickLimit,
  buildQuickStop,
  getLimitSide,
  getStopSide,
  resolveBaseQuantity,
  splitTradeSymbol,
}
