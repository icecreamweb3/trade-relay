import { useCallback, useEffect, useState } from 'react'
import { Download, FileText, RefreshCw } from 'lucide-react'
import { api, type ApiServerLogFile, type ApiServerLogPreview } from '../api/client'
import { useTranslation } from '../i18n/translations'
import { useToastStore } from '../store/toastStore'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'
import { formatUtcTimestampToLocalString } from '../utils/datetime'


export function ServerLogsScreen() {
  const locale = useUiPreferencesStore((state) => state.locale)
  const { t } = useTranslation(locale)
  const showToast = useToastStore((state) => state.showToast)
  const [files, setFiles] = useState<ApiServerLogFile[]>([])
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [preview, setPreview] = useState<ApiServerLogPreview | null>(null)
  const [loadingFiles, setLoadingFiles] = useState(true)
  const [loadingPreview, setLoadingPreview] = useState(false)
  const [downloading, setDownloading] = useState(false)

  const loadFiles = useCallback(async () => {
    setLoadingFiles(true)
    try {
      const nextFiles = await api.getServerLogs()
      setFiles(nextFiles)
      setSelectedName((current) => {
        if (current && nextFiles.some((file) => file.name === current)) return current
        return nextFiles[0]?.name ?? null
      })
      return nextFiles
    } catch (error) {
      showToast('error', getApiError(error, t('serverLogs.error.loadList')))
      return null
    } finally {
      setLoadingFiles(false)
    }
  }, [showToast, t])

  const loadPreview = useCallback(async (filename: string) => {
    setLoadingPreview(true)
    try {
      setPreview(await api.getServerLogPreview(filename))
    } catch (error) {
      setPreview(null)
      showToast('error', getApiError(error, t('serverLogs.error.loadDetail')))
    } finally {
      setLoadingPreview(false)
    }
  }, [showToast, t])

  useEffect(() => {
    void loadFiles()
  }, [loadFiles])

  useEffect(() => {
    if (selectedName) void loadPreview(selectedName)
    else setPreview(null)
  }, [loadPreview, selectedName])

  const handleRefresh = async () => {
    const nextFiles = await loadFiles()
    if (nextFiles && selectedName && nextFiles.some((file) => file.name === selectedName)) {
      await loadPreview(selectedName)
    }
  }

  const handleDownload = async () => {
    if (!selectedName || downloading) return
    setDownloading(true)
    try {
      const result = await api.downloadServerLog(selectedName)
      if (result.ok) showToast('success', t('serverLogs.downloaded'))
      else if (!result.canceled) showToast('error', result.error || t('serverLogs.error.download'))
    } catch (error) {
      showToast('error', getApiError(error, t('serverLogs.error.download')))
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="h-full min-h-0 flex flex-col bg-[#0d1219] text-[#dde4ef]">
      <div className="flex items-center gap-2 border-b border-[#2a303c] px-4 py-3 shrink-0">
        <div className="text-sm font-semibold tracking-[0.16em] uppercase text-[#8b94a5]">{t('serverLogs.title')}</div>
        <div className="ml-auto flex items-center gap-2">
          <ToolbarButton onClick={() => void handleRefresh()} disabled={loadingFiles || loadingPreview}>
            <RefreshCw size={13} className={loadingFiles ? 'animate-spin' : ''} />
            {t('serverLogs.refresh')}
          </ToolbarButton>
          <ToolbarButton onClick={() => void handleDownload()} disabled={!selectedName || downloading} primary>
            <Download size={13} />
            {downloading ? t('serverLogs.downloading') : t('serverLogs.download')}
          </ToolbarButton>
        </div>
      </div>

      <div className="flex flex-1 min-h-0">
        <aside className="w-[340px] min-w-[280px] border-r border-[#2a303c] flex flex-col min-h-0">
          <div className="px-3 py-2 text-[11px] uppercase tracking-[0.12em] text-[#7f8999] bg-[#151a22] border-b border-[#2a303c]">
            {t('serverLogs.fileList')} · {files.length}
          </div>
          <div className="flex-1 min-h-0 overflow-auto">
            {files.length === 0 ? (
              <div className="px-4 py-8 text-center text-xs text-[#747e8e]">
                {loadingFiles ? t('serverLogs.loading') : t('serverLogs.empty')}
              </div>
            ) : files.map((file) => {
              const selected = file.name === selectedName
              return (
                <button
                  key={file.name}
                  type="button"
                  onClick={() => setSelectedName(file.name)}
                  className={`w-full border-b border-[#222935] px-3 py-3 text-left transition-colors ${
                    selected ? 'bg-[#12233a]' : 'hover:bg-[#131b25]'
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <FileText size={15} className={selected ? 'mt-0.5 shrink-0 text-[#55a2ff]' : 'mt-0.5 shrink-0 text-[#778294]'} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-mono text-[12px] text-[#e2e8f2]" title={file.name}>{file.name}</div>
                      <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-[#7f8999]">
                        <span>{formatBytes(file.size)}</span>
                        <span>{formatUtcTimestampToLocalString(file.modified_at)}</span>
                      </div>
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        </aside>

        <section className="flex-1 min-w-0 min-h-0 flex flex-col">
          <div className="h-10 shrink-0 flex items-center gap-3 border-b border-[#2a303c] bg-[#111720] px-3 text-[11px] text-[#8b94a5]">
            <span className="truncate font-mono text-[#dbe3ef]">{preview?.name || selectedName || t('serverLogs.noSelection')}</span>
            {preview && <span className="ml-auto shrink-0">{formatBytes(preview.size)} · {formatUtcTimestampToLocalString(preview.modified_at)}</span>}
          </div>
          {preview?.truncated && (
            <div className="shrink-0 border-b border-[#5f4c1d] bg-[#2a2415] px-3 py-1.5 text-[11px] text-[#e7c45b]">
              {t('serverLogs.truncated')}
            </div>
          )}
          <div className="flex-1 min-h-0 overflow-auto bg-[#090d12]">
            {loadingPreview ? (
              <div className="p-5 text-xs text-[#7f8999]">{t('serverLogs.loadingDetail')}</div>
            ) : preview ? (
              <pre className="selectable min-w-max p-4 font-mono text-[11px] leading-5 text-[#cbd4e1] whitespace-pre">{preview.content}</pre>
            ) : (
              <div className="p-5 text-xs text-[#7f8999]">{t('serverLogs.noSelection')}</div>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}


function ToolbarButton({
  children,
  onClick,
  disabled = false,
  primary = false,
}: {
  children: React.ReactNode
  onClick: () => void
  disabled?: boolean
  primary?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex h-7 items-center gap-1.5 rounded border px-2.5 text-[11px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        primary
          ? 'border-[#296bc1] bg-[#18549d] text-white hover:bg-[#2166b8]'
          : 'border-[#343c49] bg-[#1b222c] text-[#c7d0dd] hover:bg-[#252e3a]'
      }`}
    >
      {children}
    </button>
  )
}


function formatBytes(value: number) {
  if (!Number.isFinite(value) || value < 0) return '-'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}


function getApiError(error: unknown, fallback: string) {
  const candidate = error as { message?: string; response?: { data?: { detail?: string } } }
  return candidate?.response?.data?.detail || candidate?.message || fallback
}
