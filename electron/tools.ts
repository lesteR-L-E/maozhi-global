import type { ToolSchema } from './runtime'

export type ToolResult = { ok: boolean; content: string }

export type ToolDefinition = {
  schema: ToolSchema
  execute: (args: Record<string, unknown>) => Promise<ToolResult> | ToolResult
}

const greetings: Record<string, string> = {
  正式: '您好！欢迎使用贸智全球决策工作台，我是您的智能决策助手，很高兴为您服务。',
  亲切: '你好呀！很高兴见到你，我是贸智全球助手，想聊点什么都可以～',
  活泼: '嗨～欢迎来到贸智全球！我是你的决策搭子，随时开聊！'
}

// 第一版唯一的演示工具：用户打招呼时由模型触发，用于跑通完整的工具调用链路
const greetUser: ToolDefinition = {
  schema: {
    type: 'function',
    function: {
      name: 'greet_user',
      description: '向用户生成一段友好的欢迎语。当用户发送问候语（如“你好”“您好”“hi”“hello”）时调用。',
      parameters: {
        type: 'object',
        properties: {
          style: { type: 'string', enum: ['正式', '亲切', '活泼'], description: '欢迎语风格，默认“亲切”' }
        },
        required: []
      }
    }
  },
  execute: (args) => {
    const style = typeof args.style === 'string' && args.style in greetings ? args.style : '亲切'
    return { ok: true, content: greetings[style] }
  }
}

export const toolRegistry: ToolDefinition[] = [greetUser]

export function toolSchemas(): ToolSchema[] {
  return toolRegistry.map((tool) => tool.schema)
}

export async function executeTool(name: string, argsText: string): Promise<ToolResult> {
  const tool = toolRegistry.find((item) => item.schema.function.name === name)
  if (!tool) return { ok: false, content: `未注册的工具：${name}` }
  try {
    const args = argsText.trim() ? (JSON.parse(argsText) as Record<string, unknown>) : {}
    return await tool.execute(args)
  } catch (error) {
    return { ok: false, content: `工具执行出错：${error instanceof Error ? error.message : String(error)}` }
  }
}
