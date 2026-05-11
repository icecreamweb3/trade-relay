/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'bg-primary': '#1e1e1e',
        'bg-sidebar': '#252526',
        'bg-panel': '#1e1e1e',
        'border-default': '#3e3e42',
        'text-primary': '#cccccc',
        'text-muted': '#858585',
        'accent': '#007acc',
        'green': '#4ec9b0',
        'yellow': '#dcdcaa',
        'red': '#f44747',
        'orange': '#ce9178',
        'buy': '#00c853',
        'sell': '#ff1744',
      },
      fontFamily: {
        mono: ['Consolas', 'Monaco', 'Courier New', 'monospace'],
      }
    },
  },
  plugins: [],
}
