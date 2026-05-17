import { create } from 'zustand'
import { Locale } from '../i18n/translations'

export type OrderBookDepthMode = 'level' | 'cumulative'

const UI_LOCALE_STORAGE_KEY = 'trade-relay:ui-locale'
const ORDER_BOOK_DEPTH_MODE_STORAGE_KEY = 'trade-relay:order-book-depth-mode'

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

interface UiPreferencesStore {
  locale: Locale
  orderBookDepthMode: OrderBookDepthMode
  setLocale: (locale: Locale) => void
  setOrderBookDepthMode: (mode: OrderBookDepthMode) => void
}

export const useUiPreferencesStore = create<UiPreferencesStore>((set) => ({
  locale: getPreferredLocale(),
  orderBookDepthMode: readStoredOrderBookDepthMode(),
  setLocale: (locale) => {
    writeStoredLocale(locale)
    set({ locale })
  },
  setOrderBookDepthMode: (mode) => {
    writeStoredOrderBookDepthMode(mode)
    set({ orderBookDepthMode: mode })
  },
}))
