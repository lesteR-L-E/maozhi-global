import { existsSync } from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const projectRoot = fileURLToPath(new URL('..', import.meta.url))
const pythonPath = process.platform === 'win32'
  ? path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
  : path.join(projectRoot, '.venv', 'bin', 'python')
const smokeTestPath = path.join(projectRoot, 'python', 'smoke_test.py')

if (!existsSync(pythonPath)) {
  console.error(`Project Python interpreter not found: ${pythonPath}`)
  process.exit(1)
}

const result = spawnSync(pythonPath, [smokeTestPath], {
  cwd: projectRoot,
  env: process.env,
  stdio: 'inherit'
})

if (result.error) {
  console.error(result.error.message)
  process.exit(1)
}

process.exit(result.status ?? 1)
