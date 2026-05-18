import { create } from 'zustand'
import { Locale } from '../i18n/translations'

export type OrderBookDepthMode = 'level' | 'cumulative'

const UI_LOCALE_STORAGE_KEY = 'trade-relay:ui-locale'
const ORDER_BOOK_DEPTH_MODE_STORAGE_KEY = 'trade-relay:order-book-depth-mode'
const CHART_ORDER_MARKERS_VISIBLE_STORAGE_KEY = 'trade-relay:chart-order-markers-visible'
const CHART_ORDER_MARKER_LABELS_VISIBLE_STORAGE_KEY = 'trade-relay:chart-order-marker-labels-visible'

function readStoredLocale(): Locale | null {
  try {
    const raw = window.localStorage.getItem(UI_LOCALE_STORAGE_KEY)
    return raw === 'en' || raw === 'zh-CN' ? raw : null
  } catch {
    return null
  }
}

function readElectronLocale(): Locale {
  return window.electronAPI?.uiLang === 'en' ? 'en' : 'zh-CN'
}

export function getPreferredLocale(): Locale {
  return readStoredLocale() ?? readElectronLocale()
}

function writeStoredLocale(locale: Locale) {
  try {
    window.localStorage.setItem(UI_LOCALE_STORAGE_KEY, locale)
  } catch {
    // Ignore storage failures and keep the in-memory preference.
  }
}

function readStoredOrderBookDepthMode(): OrderBookDepthMode {
  try {
    const raw = window.localStorage.getItem(ORDER_BOOK_DEPTH_MODE_STORAGE_KEY)
    return raw === 'level' ? 'level' : 'cumulative'
  } catch {
    return 'cumulative'
  }
}

function writeStoredOrderBookDepthMode(mode: OrderBookDepthMode) {
  try {
    window.localStorage.setItem(ORDER_BOOK_DEPTH_MODE_STORAGE_KEY, mode)
  } catch {
    // Ignore storage failures and keep the in-memory preference.
  }
}

function readStoredChartOrderMarkersVisible(): boolean {
  try {
    const raw = window.localStorage.getItem(CHART_ORDER_MARKERS_VISIBLE_STORAGE_KEY)
    if (raw == null) return true
    return raw !== 'false'
  } catch {
    return true
  }
}

function writeStoredChartOrderMarkersVisible(visible: boolean) {
  try {
    window.localStorage.setItem(CHART_ORDER_MARKERS_VISIBLE_STORAGE_KEY, String(visible))
  } catch {
    // Ignore storage failures and keep the in-memory preference.
  }
}

function readStoredChartOrderMarkerLabelsVisible(): boolean {
  try {
    const raw = window.localStorage.getItem(CHART_ORDER_MARKER_LABELS_VISIBLE_STORAGE_KEY)
    if (raw == null) return true
    return raw !== 'false'
  } catch {
    return true
  }
}

function writeStoredChartOrderMarkerLabelsVisible(visible: boolean) {
  try {
    window.localStorage.setItem(CHART_ORDER_MARKER_LABELS_VISIBLE_STORAGE_KEY, String(visible))
  } catch {
    // Ignore storage failures and keep the in-memory preference.
  }
}

interface UiPreferencesStore {
  locale: Locale
  orderBookDepthMode: OrderBookDepthMode
  chartOrderMarkersVisible: boolean
  chartOrderMarkerLabelsVisible: boolean
  setLocale: (locale: Locale) => void
  setOrderBookDepthMode: (mode: OrderBookDepthMode) => void
  setChartOrderMarkersVisible: (visible: boolean) => void
  setChartOrderMarkerLabelsVisible: (visible: boolean) => void
}

export const useUiPreferencesStore = create<UiPreferencesStore>((set) => ({
  locale: getPreferredLocale(),
  orderBookDepthMode: readStoredOrderBookDepthMode(),
  chartOrderMarkersVisible: readStoredChartOrderMarkersVisible(),
  chartOrderMarkerLabelsVisible: readStoredChartOrderMarkerLabelsVisible(),
  setLocale: (locale) => {
    writeStoredLocale(locale)
    set({ locale })
  },
  setOrderBookDepthMode: (mode) => {
    writeStoredOrderBookDepthMode(mode)
    set({ orderBookDepthMode: mode })
  },
  setChartOrderMarkersVisible: (visible) => {
    writeStoredChartOrderMarkersVisible(visible)
    set({ chartOrderMarkersVisible: visible })
  },
  setChartOrderMarkerLabelsVisible: (visible) => {
    writeStoredChartOrderMarkerLabelsVisible(visible)
    set({ chartOrderMarkerLabelsVisible: visible })
  },
}))
