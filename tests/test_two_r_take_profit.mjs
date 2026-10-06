import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const sourceUrl = new URL('../src/utils/takeProfit.ts', import.meta.url)
const source = await readFile(sourceUrl, 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2020, target: ts.ScriptTarget.ES2020 },
}).outputText
const { calculateTwoRTakeProfit } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`
)

test('uses the configured minimum point spread when 2R is smaller', () => {
  assert.equal(calculateTwoRTakeProfit({ side: 'LONG', entry_price: 80_000 }, 79_900, 500), 80_500)
  assert.equal(calculateTwoRTakeProfit({ side: 'SHORT', entry_price: 80_000 }, 80_100, 500), 79_500)
})

test('keeps the 2R target when its spread is larger than the minimum', () => {
  assert.equal(calculateTwoRTakeProfit({ side: 'LONG', entry_price: 80_000 }, 79_600, 500), 80_800)
  assert.equal(calculateTwoRTakeProfit({ side: 'SHORT', entry_price: 80_000 }, 80_400, 500), 79_200)
})

test('returns no short target when the minimum spread would make it non-positive', () => {
  assert.equal(calculateTwoRTakeProfit({ side: 'SHORT', entry_price: 100 }, 110, 500), null)
})
