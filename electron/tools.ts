import { spawn } from 'node:child_process'
import path from 'node:path'
import { app } from 'electron'
import type { ToolSchema } from './runtime'

export type EvidenceItem = { source: string; period?: string; scope?: string; note?: string }
export type DataQuality = {
  status: 'OK' | 'PARTIAL' | 'UNAVAILABLE'
  sourceType: 'SIMULATED_DEMO' | 'PUBLIC_DATA' | 'MIXED'
  missing: string[]
  warnings: string[]
  forecastMethod?: string
}
export type StructuredToolResult<T = unknown> = {
  ok: boolean
  summary: string
  data?: T
  evidence: EvidenceItem[]
  dataQuality: DataQuality
  warnings: string[]
  error?: { code: string; message: string }
}
export type ToolResult = { ok: boolean; content: string; structured?: StructuredToolResult }
export type ToolExecutor = (args: Record<string, unknown>) => Promise<ToolResult> | ToolResult

export type ToolDefinition = {
  schema: ToolSchema
  execute: ToolExecutor
}

const QUALITY: DataQuality = { status: 'OK', sourceType: 'MIXED', missing: [], warnings: [] }
// 开发时优先使用项目虚拟环境；打包时由构建流程提供对应平台的Python运行时。
const projectPython = process.platform === 'win32'
  ? path.join(__dirname, '../.venv/Scripts/python.exe')
  : path.join(__dirname, '../.venv/bin/python')
const pythonExecutable = process.env.PYTHON_BIN
  || (app.isPackaged ? (process.platform === 'win32' ? 'python' : 'python3') : projectPython)
const workerPath = app.isPackaged
  ? path.join(process.resourcesPath, 'app.asar.unpacked/python/analysis_worker.py')
  : path.join(__dirname, '../python/analysis_worker.py')
const defaultDatabasePath = app.isPackaged
  ? path.join(process.resourcesPath, 'app.asar.unpacked/data/maozhi-real.duckdb')
  : path.join(__dirname, '../data/maozhi-real.duckdb')
const databasePath = process.env.MAOZHI_DATABASE || defaultDatabasePath

// Worker和数据库都支持通过环境变量替换，便于后续接入数据组的正式数据包。

async function callPython(method: string, params: Record<string, unknown>): Promise<StructuredToolResult> {
  return new Promise((resolve) => {
    const child = spawn(pythonExecutable, [workerPath], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: {
        ...process.env,
        MAOZHI_DATABASE: databasePath,
        PYTHONUTF8: '1',
        PYTHONIOENCODING: 'utf-8'
      }
    })
    let stdout = ''
    let stderr = ''
    let settled = false
    const finish = (result: StructuredToolResult) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(result)
    }
    const timer = setTimeout(() => {
      child.kill()
      finish({ ok: false, summary: 'Python分析Worker超时。', evidence: [], dataQuality: { ...QUALITY, status: 'UNAVAILABLE', warnings: ['Worker超过60秒未返回'] }, warnings: ['请检查Python运行环境。'], error: { code: 'PYTHON_TIMEOUT', message: '分析Worker超时' } })
    }, 60000)
    child.stdout.on('data', (chunk) => { stdout += chunk.toString() })
    child.stderr.on('data', (chunk) => { stderr += chunk.toString() })
    child.on('error', (error) => finish({ ok: false, summary: '无法启动Python分析Worker。', evidence: [], dataQuality: { ...QUALITY, status: 'UNAVAILABLE', warnings: [stderr || error.message] }, warnings: ['请安装Python 3并确认PYTHON_BIN配置。'], error: { code: 'PYTHON_START_FAILED', message: error.message } }))
    child.on('close', () => {
      if (settled) return
      try { finish(JSON.parse(stdout) as StructuredToolResult) }
      catch { finish({ ok: false, summary: 'Python分析Worker返回了无效结果。', evidence: [], dataQuality: { ...QUALITY, status: 'UNAVAILABLE', warnings: [stderr || '无JSON输出'] }, warnings: ['分析结果无法解析。'], error: { code: 'PYTHON_INVALID_OUTPUT', message: stderr || '无效JSON输出' } }) }
    })
    child.stdin.write(JSON.stringify({ method, params }))
    child.stdin.end()
  })
}

function wrap(result: StructuredToolResult): ToolResult {
  return {
    ok: result.ok,
    content: JSON.stringify(result),
    structured: result
  }
}

function textArg(args: Record<string, unknown>, key: string): string | undefined {
  const value = args[key]
  if (typeof value !== 'string') return undefined
  const trimmed = value.trim()
  return trimmed ? trimmed : undefined
}
function invalid(summary: string, code: string, message: string, warning: string): ToolResult {
  return wrap({ ok: false, summary, evidence: [], dataQuality: { ...QUALITY, status: 'UNAVAILABLE' }, warnings: [warning], error: { code, message } })
}

const resolveProduct: ToolDefinition = {
  schema: {
    type: 'function',
    function: {
      name: 'resolve_product',
      description: '在HS维表中将商品名称或HS编码解析为候选HS编码。中文商品可同时提供英文检索词。所有候选均来自数据库，名称检索结果必须让用户确认。',
      parameters: {
        type: 'object',
        properties: {
          product_name: { type: 'string', description: '用户提供的商品名称或HS编码' },
          search_terms_en: { type: 'string', description: '可选。根据用户原始描述生成的简洁英文商品检索词，只用于检索数据库，不能直接填写或猜测HS编码。' }
        },
        required: ['product_name']
      }
    }
  },
  execute: async (args) => {
    const productName = textArg(args, 'product_name') || ''
    const searchTermsEn = textArg(args, 'search_terms_en')
    const result = await callPython('resolve_product', {
      product_name: productName,
      ...(searchTermsEn ? { search_terms_en: searchTermsEn } : {})
    })
    return wrap(result)
  }
}

const analyzeMarkets: ToolDefinition = {
  schema: {
    type: 'function',
    function: {
      name: 'analyze_markets',
      description: '基于已确认HS编码分析数据库支持的RCEP15市场，返回地图数据、贸易指标、关税、宏观环境、预测和综合评分。必须先有confirmed_hs6；可用markets指定RCEP国家ISO3列表；数据不足必须降级并说明。',
      parameters: {
        type: 'object',
        properties: {
          confirmed_hs6: { type: 'string', description: '用户确认的6位HS编码' },
          markets: { type: 'array', items: { type: 'string' }, description: '可选RCEP市场ISO3列表，默认数据库支持的全部RCEP15国家' },
          include_forecast: { type: 'boolean', description: '是否计算预测，默认true' }
        },
        required: ['confirmed_hs6']
      }
    }
  },
  execute: async (args) => {
    const hs6 = textArg(args, 'confirmed_hs6')
    if (!hs6 || !/^\d{6}$/.test(hs6)) return invalid('市场分析需要用户确认的6位HS编码。', 'HS_CONFIRMATION_REQUIRED', 'confirmed_hs6必须是6位数字', '请先调用resolve_product并让用户确认HS编码。')
    return wrap(await callPython('analyze_markets', { ...args, confirmed_hs6: hs6 }))
  }
}

const analyzeCustomers: ToolDefinition = {
  schema: {
    type: 'function',
    function: {
      name: 'analyze_customers',
      description: '分析项目客户数据：RFM+G+I、K-means客户分群、交易价值S/A/B/C、流失风险规则评分和画像。当前客户与交易数据为比赛模拟数据，不能表述为真实客户或流失概率。',
      parameters: {
        type: 'object',
        properties: {
          market: { type: 'string', description: '可选ISO3市场，用于联动筛选' },
          observation_date: { type: 'string', description: '可选观察日，默认2025-12-31' }
        },
        required: []
      }
    }
  },
  execute: async (args) => wrap(await callPython('analyze_customers', args))
}

const recommendOperations: ToolDefinition = {
  schema: {
    type: 'function',
    function: {
      name: 'recommend_operations',
      description: '根据市场分析和客户分析生成市场×客户策略。必须提供前两个工具的结构化data。只允许动作方向，禁止具体价格、折扣、账期和赠品。',
      parameters: {
        type: 'object',
        properties: {
          market_analysis: { type: 'object', description: 'analyze_markets返回的data对象' },
          customer_analysis: { type: 'object', description: 'analyze_customers返回的data对象' },
          target_market: { type: 'string', description: '目标市场ISO3' }
        },
        required: ['market_analysis', 'customer_analysis']
      }
    }
  },
  execute: async (args) => {
    if (!args.market_analysis || !args.customer_analysis) return invalid('生成策略前必须先完成市场分析和客户分析。', 'ANALYSIS_PREREQUISITE_REQUIRED', '缺少市场或客户分析结果', '请先调用analyze_markets和analyze_customers。')
    return wrap(await callPython('recommend_operations', args))
  }
}

export const toolRegistry: ToolDefinition[] = [resolveProduct, analyzeMarkets, analyzeCustomers, recommendOperations]
export function toolSchemas(): ToolSchema[] { return toolRegistry.map((tool) => tool.schema) }

export async function executeTool(name: string, argsText: string): Promise<ToolResult> {
  const tool = toolRegistry.find((item) => item.schema.function.name === name)
  if (!tool) return invalid(`未注册的工具：${name}`, 'UNKNOWN_TOOL', name, '请使用已注册的四个业务工具。')
  try {
    const parsed: unknown = argsText.trim() ? JSON.parse(argsText) : {}
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('工具参数必须是JSON对象')
    return await tool.execute(parsed as Record<string, unknown>)
  } catch (error) {
    return invalid('工具参数解析失败。', 'INVALID_TOOL_ARGUMENTS', error instanceof Error ? error.message : String(error), '请修正工具参数后重试。')
  }
}
