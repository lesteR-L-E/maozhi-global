// Render regression coverage without starting Electron or requiring a browser.
// Run: node scripts/check-workbench.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');
const { renderToString } = require('react-dom/server');

for (const extension of ['.ts', '.tsx']) {
  require.extensions[extension] = (module, filename) => {
    const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
      fileName: filename,
    });
    module._compile(outputText, filename);
  };
}
require.extensions['.css'] = () => {};
const { Workbench } = require(path.join(__dirname, '../src/workbench/Workbench.tsx'));
const { readAnalysis } = require(path.join(__dirname, '../src/workbench/data.ts'));
let seen = false;
global.localStorage = { getItem: () => seen ? '1' : null };
const render = messages => renderToString(React.createElement(Workbench, { messages, onPrompt() {} }));
const result = (name, data) => ({ id: name, kind: 'tool', name, status: 'done', result: JSON.stringify({ ok: true, data }) });
const market = { market: 'PHL', name_cn: '菲律宾', rank: 1, total_score: 70, dimensions: {}, forecast: { values: [] } };

assert.match(render([]), /进入工作台/);
seen = true;
assert.match(render([]), /你的下一站，在哪里/);
assert.match(render([{ id: 'pending', kind: 'tool', name: 'analyze_markets', status: 'running' }]), /正在寻找市场机会/);
assert.match(render([{ id: 'failed', kind: 'tool', name: 'analyze_markets', status: 'failed', result: '{"ok":false}' }]), /本次市场分析未完成/);
assert.match(render([result('analyze_markets', { hs6: '851671', markets: [market] })]), /菲律宾/);
assert.match(render([result('analyze_customers', { customers: [] })]), /你的下一站，在哪里/);
assert.match(render([result('recommend_operations', { target_market: 'PHL', hs6: '851671', cards: [] })]), /你的下一站，在哪里/);
assert.equal(readAnalysis([result('analyze_markets', { hs6: '851671', markets: [market] }), result('resolve_product', { candidates: [] })]).markets, undefined);
console.log('PASS workbench render: first launch, returning empty session, loading, failure, market-only, customer-only, strategy-only; product reset');
