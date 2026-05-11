import React from 'react'

interface ErrorBoundaryState { hasError: boolean; errorMessage: string }

export class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  ErrorBoundaryState
> {
  constructor(props: { children: React.ReactNode }) {
    super(props)
    this.state = { hasError: false, errorMessage: '' }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, errorMessage: error?.stack || error?.message || 'Unknown error' }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, info)
    window.electronAPI?.resizeBinancePanel(0)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="h-full flex flex-col items-center justify-center text-red-400 p-8">
          <h2 className="text-lg font-bold mb-4">渲染错误</h2>
          <pre className="text-xs text-left bg-[#252526] p-4 rounded max-w-2xl w-full overflow-auto selectable">
            {this.state.errorMessage}
          </pre>
          <button
            className="mt-4 px-4 py-2 bg-[#007acc] text-white rounded hover:bg-blue-600"
            onClick={() => this.setState({ hasError: false, errorMessage: '' })}
          >
            重试
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
