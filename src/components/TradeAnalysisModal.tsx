/**
 * TradeAnalysisModal — 展示订单记录的交易分析结果（统计指标 + 最大盈利/亏损详情）
 */
import { useEffect } from 'react'
import { X } from 'lucide-react'
import { AnalyzedTrade, TradeAnalysis } from '../utils/tradeAnalysis'
import { parseUtcTimestamp } from '../utils/datetime'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'
import { useTranslation } from '../i18n/translations'

type Translate = (key: string, vars?: Record<string, string | number>) => string

function fmtTime(ts?: string): string {
  if (!ts) return '—'
  const d = parseUtcTimestamp(ts)
  if (!d) return ts
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function fmtSigned(value: number, dp = 4): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(dp)}`
}

function fmtNotional(value: number): string {
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtQty(value: number): string {
  return value.toFixed(6).replace(/\.?0+$/, '')
}

function fmtCommissions(commissions: Record<string, number>): string {
  const entries = Object.entries(commissions)
  if (entries.length === 0) return '—'
  return entries.map(([asset, amount]) => `${amount.toFixed(4)} ${asset}`).join(' / ')
}

function StatItem({ label, value, valueClass = 'text-[#dde4ef]' }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="rounded border border-[#2a2f3a] bg-[#1e222b] px-3 py-2">
      <div className="text-[11px] text-[#8b94a5]">{label}</div>
      <div className={`mt-1 font-mono text-sm font-semibold ${valueClass}`}>{value}</div>
    </div>
  )
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 py-1">
      <span className="w-16 shrink-0 text-[11px] leading-5 text-[#8b94a5]">{label}</span>
      <div className="min-w-0 flex-1 font-mono text-[12px] leading-5 text-[#dde4ef]">{children}</div>
    </div>
  )
}

function TripDetailCard({
  title,
  titleClass,
  trip,
  pnlLabel,
  t,
}: {
  title: string
  titleClass: string
  trip: AnalyzedTrade | null
  pnlLabel: string
  t: Translate
}) {
  return (
    <div className="rounded border border-[#2a2f3a] bg-[#1e222b] px-3 py-2">
      <div className={`text-[12px] font-semibold ${titleClass}`}>{title}</div>
      {trip ? (
        <div className="mt-1 divide-y divide-[#262b36]">
          <DetailRow label={t('log.user')}>{trip.username || '—'}</DetailRow>
          <DetailRow label={t('log.symbol')}>{trip.symbol}</DetailRow>
          <DetailRow label={t('log.analyze.positionQty')}>{fmtQty(trip.quantity)}</DetailRow>
          <DetailRow label={pnlLabel}>
            <span className={trip.pnl > 0 ? 'text-[#0ecb81]' : trip.pnl < 0 ? 'text-[#f6465d]' : ''}>
              {fmtSigned(trip.pnl)}
            </span>
          </DetailRow>
          <DetailRow label={t('trade.commission')}>{fmtCommissions(trip.commissions)}</DetailRow>
          <DetailRow label={t('log.analyze.entryTime')}>
            {trip.entryTimes.length > 0
              ? trip.entryTimes.map((ts, i) => <div key={i}>{fmtTime(ts)}</div>)
              : '—'}
          </DetailRow>
          <DetailRow label={t('log.analyze.exitTime')}>
            {trip.exitTimes.length > 0
              ? trip.exitTimes.map((ts, i) => <div key={i}>{fmtTime(ts)}</div>)
              : '—'}
          </DetailRow>
        </div>
      ) : (
        <div className="py-3 text-center text-[12px] text-[#555]">—</div>
      )}
    </div>
  )
}

export function TradeAnalysisModal({ analysis, onClose }: { analysis: TradeAnalysis; onClose: () => void }) {
  const locale = useUiPreferencesStore((state) => state.locale)
  const { t } = useTranslation(locale)

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[85vh] w-[760px] max-w-full overflow-auto rounded border border-[#3e3e42] bg-[#161a21] shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-[#3e3e42] bg-[#161a21] px-4 py-2.5">
          <span className="text-sm font-semibold text-[#cccccc]">{t('log.analyze.title')}</span>
          <button
            type="button"
            onClick={onClose}
            title={t('common.close')}
            className="flex h-7 w-7 items-center justify-center rounded text-[#8b94a5] hover:bg-[#252b36] hover:text-[#dde4ef]"
          >
            <X size={15} />
          </button>
        </div>

        <div className="p-4">
          <div className="grid grid-cols-3 gap-2">
            <StatItem label={t('log.analyze.tradeCount')} value={String(analysis.tradeCount)} />
            <StatItem label={t('log.analyze.tradeAmount')} value={fmtNotional(analysis.totalNotional)} />
            <StatItem
              label={t('log.analyze.totalPnl')}
              value={fmtSigned(analysis.totalPnl)}
              valueClass={analysis.totalPnl > 0 ? 'text-[#0ecb81]' : analysis.totalPnl < 0 ? 'text-[#f6465d]' : 'text-[#dde4ef]'}
            />
            <StatItem
              label={t('log.analyze.winRate')}
              value={analysis.winRate != null ? `${(analysis.winRate * 100).toFixed(1)}%` : '—'}
            />
            <StatItem label={t('log.analyze.winCount')} value={String(analysis.winCount)} valueClass="text-[#0ecb81]" />
            <StatItem label={t('log.analyze.lossCount')} value={String(analysis.lossCount)} valueClass="text-[#f6465d]" />
            <StatItem
              label={t('log.analyze.maxProfit')}
              value={analysis.maxProfitTrip ? fmtSigned(analysis.maxProfitTrip.pnl) : '—'}
              valueClass="text-[#0ecb81]"
            />
            <StatItem
              label={t('log.analyze.maxLoss')}
              value={analysis.maxLossTrip ? fmtSigned(analysis.maxLossTrip.pnl) : '—'}
              valueClass="text-[#f6465d]"
            />
            <StatItem label={t('log.analyze.commission')} value={fmtCommissions(analysis.commissions)} />
          </div>

          <div className="mt-3 grid grid-cols-2 gap-2">
            <TripDetailCard
              title={t('log.analyze.maxProfitDetail')}
              titleClass="text-[#0ecb81]"
              trip={analysis.maxProfitTrip}
              pnlLabel={t('log.analyze.profit')}
              t={t}
            />
            <TripDetailCard
              title={t('log.analyze.maxLossDetail')}
              titleClass="text-[#f6465d]"
              trip={analysis.maxLossTrip}
              pnlLabel={t('log.analyze.loss')}
              t={t}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
