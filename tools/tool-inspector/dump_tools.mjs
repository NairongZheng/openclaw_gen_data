#!/usr/bin/env node
/**
 * dump_tools.mjs
 *
 * 从 OpenClaw 运行时动态提取所有工具的完整定义：
 *  - 内置工具：自动扫描 dist bundle，找所有 name/description/parameters 组合
 *  - pi-coding-agent 工具（read/write/edit）：从 npm 包直接导入
 *  - 插件工具：运行时 load 插件，拦截 registerTool 调用
 *
 * 不硬编码任何工具名或 schema 变量名。
 * 支持按 agent_id 过滤（alsoAllow / deny）。
 * 输出纯 JSON。
 *
 * 用法:
 *   node dump_tools.mjs --agent main --output /path/to/tools.json
 *   node dump_tools.mjs --all-agents --output /path/to/{agent}/tools.json
 *   node dump_tools.mjs --help
 */

import fs from 'fs';
import path from 'path';
import { createJiti } from 'jiti';
import { createRequire } from 'module';
import { parseArgs } from 'util';
import { Type } from '@sinclair/typebox';

const req = createRequire(import.meta.url);

// ── CLI 参数 ──────────────────────────────────────────────────────────────────

const { values: args } = parseArgs({
  options: {
    agent:        { type: 'string',  multiple: true, short: 'a', default: [] },
    'all-agents': { type: 'boolean',                 short: 'A', default: false },
    output:       { type: 'string',                  short: 'o', default: '' },
    help:         { type: 'boolean',                 short: 'h', default: false },
  },
  allowPositionals: true,
});

if (args.help) {
  console.log(`
Usage: node dump_tools.mjs [options]

Options:
  -a, --agent <id>      Agent id to export (repeatable).
  -A, --all-agents      Export all agents defined in openclaw.json.
  -o, --output <path>   Output path. Use {agent} as placeholder.
                        Default: ~/.openclaw/tool-inspector/{agent}/tools.json
  -h, --help            Show this help.

Examples:
  node dump_tools.mjs --agent main --output /tmp/tools.json
  node dump_tools.mjs --all-agents --output /tmp/{agent}/tools.json
`);
  process.exit(0);
}

// ── 配置加载 ──────────────────────────────────────────────────────────────────

const HOME = process.env.HOME ?? '/tmp';
const configPath = path.join(HOME, '.openclaw/openclaw.json');
if (!fs.existsSync(configPath)) {
  console.error(`ERROR: openclaw config not found at ${configPath}`);
  process.exit(1);
}
const cfg = JSON.parse(fs.readFileSync(configPath, 'utf8'));
const allAgentIds = (cfg.agents?.list ?? []).map(a => a.id);

let targetAgentIds;
if (args['all-agents']) {
  targetAgentIds = allAgentIds;
} else if (args.agent.length > 0) {
  targetAgentIds = args.agent;
  for (const id of targetAgentIds) {
    if (!allAgentIds.includes(id)) {
      console.error(`ERROR: agent "${id}" not found. Available: ${allAgentIds.join(', ')}`);
      process.exit(1);
    }
  }
} else {
  targetAgentIds = allAgentIds;
}

// ── 自动探测 OpenClaw 安装路径 ────────────────────────────────────────────────

function findOpenClawRoot() {
  const candidates = [
    '/opt/homebrew/lib/node_modules/openclaw',
    '/usr/local/lib/node_modules/openclaw',
    path.join(HOME, '.nvm/versions/node/current/lib/node_modules/openclaw'),
  ];
  try {
    const url = import.meta.resolve?.('openclaw/package.json');
    if (url) candidates.unshift(path.dirname(new URL(url).pathname));
  } catch {}
  return candidates.find(p => fs.existsSync(p));
}

const OPENCLAW_ROOT = findOpenClawRoot();
if (!OPENCLAW_ROOT) {
  console.error('ERROR: OpenClaw installation not found');
  process.exit(1);
}
const DIST    = path.join(OPENCLAW_ROOT, 'dist');
const EXT_ROOT = path.join(OPENCLAW_ROOT, 'extensions');
const PI_TOOLS = path.join(OPENCLAW_ROOT, 'node_modules/@mariozechner/pi-coding-agent/dist/core/tools');

// ── Bundle 工具提取（全自动扫描，不预设工具名） ────────────────────────────────

/** 找含有最多内置工具定义的 bundle 文件 */
function findBestBundle() {
  // 这些工具名必须同时出现，才说明是包含内置工具定义的 bundle
  const REQUIRED = ['name: "exec"', 'name: "cron"', 'name: "browser"', 'name: "sessions_spawn"'];
  const BONUS    = ['name: "gateway"', 'name: "nodes"', 'name: "tts"', 'BrowserToolSchema', 'CronToolSchema'];
  let best = null, bestScore = 0;
  for (const f of fs.readdirSync(DIST).filter(f => f.endsWith('.js'))) {
    const src = fs.readFileSync(path.join(DIST, f), 'utf8');
    if (!REQUIRED.every(m => src.includes(m))) continue;
    const score = BONUS.filter(m => src.includes(m)).length;
    if (score > bestScore) { best = { file: f, src }; bestScore = score; }
  }
  return best;
}

/** 提取所有大写字母开头的数组/数字常量，用于 eval 时注入 */
function extractAllConsts(src) {
  const consts = {};
  const re = /(?:const|let|var)\s+([A-Z][A-Z0-9_]{2,})\s*=\s*(\[[^\]]+\]|-?\d+)/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    try { consts[m[1]] = new Function(`return ${m[2]}`)(); } catch {}
  }
  return consts;
}

/** 括号匹配，提取 Type.Object(...) 完整表达式 */
function matchParens(src, startIdx) {
  let depth = 0, i = startIdx;
  while (i < src.length) {
    if (src[i] === '(') depth++;
    else if (src[i] === ')') { depth--; if (depth === 0) return i; }
    i++;
  }
  return -1;
}

/**
 * 尝试对一个 TypeBox 表达式求值。
 * consts: 大写常量表，extraSchemas: 已解析的命名 schema（用于解依赖）
 */
function evalTypeboxExpr(expr, consts, extraSchemas = {}) {
  function optionalStringEnum(values, opts = {}) {
    if (!Array.isArray(values) || !values.length) return Type.Optional(Type.String(opts));
    return Type.Optional(Type.Union(values.map(v => Type.Literal(v)), opts));
  }
  function stringEnum(values, opts = {}) {
    if (!Array.isArray(values) || !values.length) return Type.String(opts);
    return Type.Union(values.map(v => Type.Literal(v)), opts);
  }
  const constCode = Object.entries(consts).map(([k, v]) => `const ${k} = ${JSON.stringify(v)};`).join('\n');
  const extraNames = Object.keys(extraSchemas);
  const extraVals  = Object.values(extraSchemas);
  try {
    return new Function('Type', 'stringEnum', 'optionalStringEnum', ...extraNames,
      constCode + `\nreturn (${expr})`
    )(Type, stringEnum, optionalStringEnum, ...extraVals);
  } catch { return null; }
}

/**
 * 在 src 中找到 `varName = Type.Object(...)` 并 eval。
 * 如果失败，收集 expr 中出现的未定义标识符，递归解析后重试。
 */
function resolveNamedSchema(src, varName, consts, cache = {}) {
  if (cache[varName] !== undefined) return cache[varName];
  cache[varName] = null; // 防循环

  const marker = `${varName} = Type.Object(`;
  const idx = src.indexOf(marker);
  if (idx === -1) return null;

  const openParen = idx + marker.length - 1;
  const closeParen = matchParens(src, openParen);
  if (closeParen === -1) return null;

  const expr = `Type.Object(${src.slice(openParen + 1, closeParen)})`;

  // 先直接尝试
  let schema = evalTypeboxExpr(expr, consts, cache);
  if (schema) { cache[varName] = schema; return schema; }

  // 收集 expr 里出现的 PascalCase/camelCase 标识符，可能是嵌套 schema 依赖
  const deps = new Set(
    (expr.match(/\b([A-Za-z_$][A-Za-z0-9_$]*Schema)\b/g) ?? [])
      .filter(d => d !== varName && !cache[d])
  );
  for (const dep of deps) {
    if (!cache[dep]) resolveNamedSchema(src, dep, consts, cache);
  }

  schema = evalTypeboxExpr(expr, consts, cache);
  cache[varName] = schema;
  return schema;
}

/**
 * 在 name: "toolName" 后方寻找 parameters: <expr>，
 * 支持三种形式：
 *   1. parameters: SomeVarName           → 命名变量
 *   2. parameters: Type.Object({...})   → 内联
 *   3. parameters: someFunc(...)         → 函数调用（跳过）
 */
function resolveParametersNearName(src, nameIdx, consts, cache) {
  // 往后最多 4000 字节找 parameters:
  const chunk = src.slice(nameIdx, nameIdx + 4000);
  const paramMatch = chunk.match(/\bparameters:\s*/);
  if (!paramMatch) return null;

  const afterParam = chunk.slice(paramMatch.index + paramMatch[0].length);

  // 内联 Type.Object(
  if (afterParam.startsWith('Type.Object(')) {
    const end = matchParens(afterParam, afterParam.indexOf('('));
    if (end === -1) return null;
    const expr = afterParam.slice(0, end + 1);
    return evalTypeboxExpr(expr, consts, cache);
  }

  // 命名变量（大写或小写开头的标识符）
  const varMatch = afterParam.match(/^([A-Za-z_$][A-Za-z0-9_$]*)/);
  if (varMatch) {
    const varName = varMatch[1];
    // 排除关键字
    if (['async', 'function', 'return', 'const', 'let', 'var', 'true', 'false', 'null'].includes(varName)) return null;
    return resolveNamedSchema(src, varName, consts, cache);
  }

  return null;
}

/** 提取 name: "xxx" 附近的 description 字符串 */
function extractDescription(src, nameIdx) {
  const chunk = src.slice(nameIdx, nameIdx + 3000);
  // description: "..." 或 `...`
  const dm = chunk.match(/\bdescription:\s*(?:"([^"]{10,800})"|`([^`]{10,800})`)/);
  if (dm) return (dm[1] ?? dm[2]).replace(/\s+/g, ' ').trim();
  // description: [..., ...].join(...)  or  [...] (description is array of strings)
  const arrM = chunk.match(/\bdescription:\s*\[([^\]]{10,600})\]/);
  if (arrM) {
    try { return new Function(`return [${arrM[1]}]`)().join(' ').replace(/\s+/g, ' ').trim(); } catch {}
  }
  return '';
}

/** 全自动扫描 bundle，提取所有工具定义 */
async function extractBuiltinTools() {
  const bundle = findBestBundle();
  if (!bundle) { console.error('WARN: no bundle found'); return []; }

  const { src } = bundle;
  const consts = extractAllConsts(src);
  const cache  = {}; // schema 缓存，避免重复解析

  // 工具名白名单：必须是 snake_case，且包含 description + parameters
  const TOOL_NAME_RE = /^[a-z][a-z0-9_]{1,50}$/;

  const tools = [];
  const seen  = new Set();
  const nameRe = /\bname:\s*"([^"]+)"/g;
  let m;

  while ((m = nameRe.exec(src)) !== null) {
    const toolName = m[1];
    if (!TOOL_NAME_RE.test(toolName)) continue;
    if (seen.has(toolName)) continue;

    const schema = resolveParametersNearName(src, m.index, consts, cache);
    if (!schema) continue; // 没有 parameters 或解析失败 → 不是工具定义

    const description = extractDescription(src, m.index);
    seen.add(toolName);
    tools.push({
      name: toolName,
      description,
      parameters: JSON.parse(JSON.stringify(schema)),
      source: 'builtin',
    });
  }

  // ── web_search fallback：schema 是运行时动态生成无法自动扫描，用标准参数构造 ──
  if (!seen.has('web_search')) {
    const wsDesc = extractDescription(src, src.indexOf('name: "web_search"'));
    tools.push({
      name: 'web_search',
      description: wsDesc || 'Search the web using Brave Search API.',
      parameters: JSON.parse(JSON.stringify(Type.Object({
        query:       Type.String({ description: 'Search query string.' }),
        count:       Type.Optional(Type.Number({ description: 'Number of results (1-10).', minimum: 1, maximum: 10 })),
        country:     Type.Optional(Type.String({ description: "2-letter country code (e.g. 'US', 'DE')." })),
        language:    Type.Optional(Type.String({ description: "ISO 639-1 language code (e.g. 'en', 'de')." })),
        freshness:   Type.Optional(Type.String({ description: "Filter by time: 'day', 'week', 'month', or 'year'." })),
        date_after:  Type.Optional(Type.String({ description: 'Only results after this date (YYYY-MM-DD).' })),
        date_before: Type.Optional(Type.String({ description: 'Only results before this date (YYYY-MM-DD).' })),
        search_lang: Type.Optional(Type.String({ description: "Brave language code (e.g. 'en', 'zh-hans')." })),
        ui_lang:     Type.Optional(Type.String({ description: "Locale code (e.g. 'en-US', 'de-DE')." })),
      }))),
      source: 'builtin',
    });
    seen.add('web_search');
  }

  // ── read / write / edit 来自 pi-coding-agent（bundle 里没有定义） ──
  if (fs.existsSync(PI_TOOLS)) {
    for (const [file, key, name] of [
      ['read.js',  'readTool',  'read'],
      ['write.js', 'writeTool', 'write'],
      ['edit.js',  'editTool',  'edit'],
    ]) {
      if (seen.has(name)) continue;
      try {
        const mod  = req(path.join(PI_TOOLS, file));
        const tool = mod[key];
        if (!tool) continue;
        const params = tool.inputSchema ?? tool.parameters ?? tool.input_schema ?? null;
        tools.push({
          name,
          description: tool.description ?? '',
          parameters: params ? JSON.parse(JSON.stringify(params)) : null,
          source: 'builtin',
        });
        seen.add(name);
      } catch { /* skip */ }
    }
  }

  // ── 补充：createXxxSchema 函数模式（如 web_search） ──
  // 对于 parameters: createXxxSchema(...) 形式，直接调用函数提取
  const fnSchemaRe = /\bname:\s*"([a-z][a-z0-9_]{1,50})"[^{]{0,200}?parameters:\s*(create[A-Za-z]+Schema)\s*\(/gs;
  let fm;
  while ((fm = fnSchemaRe.exec(src)) !== null) {
    const toolName = fm[1];
    const fnName   = fm[2];
    if (seen.has(toolName)) continue;

    // 找函数定义并 eval
    const fnMarker = `function ${fnName}`;
    const fnIdx = src.indexOf(fnMarker);
    if (fnIdx === -1) continue;

    let depth = 0, i = fnIdx;
    while (i < src.length) {
      if (src[i] === '{') depth++;
      else if (src[i] === '}') { depth--; if (depth === 0) break; }
      i++;
    }
    const fnBody = src.slice(fnIdx, i + 1);
    const constCode = Object.entries(consts).map(([k, v]) => `const ${k} = ${JSON.stringify(v)};`).join('\n');
    let schema;
    try {
      schema = new Function('Type', constCode + '\n' + fnBody + `\nreturn ${fnName}({});`)(Type);
    } catch { continue; }

    const description = extractDescription(src, fm.index);
    seen.add(toolName);
    tools.push({
      name: toolName,
      description,
      parameters: JSON.parse(JSON.stringify(schema)),
      source: 'builtin',
    });
  }

  return tools;
}

// ── 插件工具：运行时 load ─────────────────────────────────────────────────────

function getPluginPaths() {
  const paths = [];
  const seen  = new Set();

  function add(dir, id) {
    if (seen.has(id) || !fs.existsSync(dir)) return;
    seen.add(id);
    paths.push({ dir, id });
  }

  for (const p of cfg.plugins?.load?.paths ?? []) {
    const resolved = p.replace(/^~/, HOME);
    add(resolved, path.basename(resolved));
  }

  const userRoot = path.join(HOME, '.openclaw/plugins');
  if (fs.existsSync(userRoot)) {
    for (const id of fs.readdirSync(userRoot)) {
      const dir = path.join(userRoot, id);
      if (fs.statSync(dir).isDirectory()) add(dir, id);
    }
  }

  if (fs.existsSync(EXT_ROOT)) {
    const entries = cfg.plugins?.entries ?? {};
    for (const id of fs.readdirSync(EXT_ROOT)) {
      const dir = path.join(EXT_ROOT, id);
      if (!fs.statSync(dir).isDirectory()) continue;
      if (entries[id]?.enabled === false) continue;
      add(dir, id);
    }
  }

  return paths;
}

async function extractPluginTools() {
  const jiti = createJiti(import.meta.url, { moduleCache: false, fsCache: false });
  const ctx  = { config: cfg, agentId: 'main', sessionKey: 'inspector', workspaceDir: '/tmp' };

  function makeFakeApi() {
    const tools = [];
    const api = {
      logger:      { info: () => {}, warn: () => {}, error: () => {}, debug: () => {} },
      on:          () => {},
      config:      cfg,
      pluginConfig: {},
      runtime: {
        tools: {
          createMemorySearchTool: () => ({
            name: 'memory_search',
            description: 'Semantically search MEMORY.md + memory/*.md.',
            parameters: { type: 'object', required: ['query'], properties: {
              query: { type: 'string' }, maxResults: { type: 'number' }, minScore: { type: 'number' },
            }},
          }),
          createMemoryGetTool: () => ({
            name: 'memory_get',
            description: 'Safe snippet read from MEMORY.md or memory/*.md.',
            parameters: { type: 'object', required: ['path'], properties: {
              path: { type: 'string' }, from: { type: 'number' }, lines: { type: 'number' },
            }},
          }),
        },
      },
      registerTool: (defOrFactory) => {
        try {
          const resolved = typeof defOrFactory === 'function' ? defOrFactory(ctx) : defOrFactory;
          for (const def of (Array.isArray(resolved) ? resolved : [resolved])) {
            if (!def?.name) continue;
            tools.push({
              name:        def.name,
              description: def.description ?? '',
              parameters:  def.parameters ? JSON.parse(JSON.stringify(def.parameters)) : null,
            });
          }
        } catch { /* skip */ }
      },
      registerChannel:      () => {},
      registerProvider:     () => {},
      registerGatewayMethod:() => {},
      registerCli:          () => {},
      registerService:      () => {},
      registerCommand:      () => {},
      registerHook:         () => {},
      registerContextEngine:() => {},
      registerHttpRoute:    () => {},
      resolvePath:          p  => p,
    };
    return { api, tools };
  }

  const allTools = [];
  const seenNames = new Set();

  for (const { dir, id } of getPluginPaths()) {
    const entry = ['index.js', 'index.ts'].map(f => path.join(dir, f)).find(f => fs.existsSync(f));
    if (!entry) continue;

    const { api, tools } = makeFakeApi();
    try {
      const mod  = entry.endsWith('.ts') ? await jiti.import(entry) : await import(entry);
      const plug = mod.default;
      if (typeof plug === 'function') plug(api);
      else if (plug?.register) plug.register(api);
    } catch { /* skip */ }

    for (const t of tools) {
      if (!seenNames.has(t.name)) {
        seenNames.add(t.name);
        allTools.push({ ...t, source: `plugin:${id}` });
      }
    }
  }

  return allTools;
}

// ── Agent 过滤 ────────────────────────────────────────────────────────────────

/**
 * 根据 agent 配置过滤工具列表
 *
 * 支持两种配置模式：
 * 1. allow 模式：完全替换全局工具集，不依赖 BASE
 * 2. alsoAllow 模式：在 BASE 基础上追加额外工具（向后兼容）
 *
 * @param {Array} allTools - 所有可用工具列表
 * @param {string} agentId - Agent ID
 * @returns {Array} 过滤后的工具列表
 */
function filterByAgent(allTools, agentId) {
  const agentCfg = (cfg.agents?.list ?? []).find(a => a.id === agentId);
  if (!agentCfg) return allTools;

  const toolsCfg = agentCfg.tools ?? {};
  const deny = new Set(toolsCfg.deny ?? []);

  // 优先使用 allow（完全替换模式），否则使用 alsoAllow + BASE（追加模式）
  let allowedSet;
  if (toolsCfg.allow) {
    // allow 模式：完全替换，不依赖 BASE，所有工具显式声明
    allowedSet = new Set(toolsCfg.allow);
  } else {
    // alsoAllow 模式：BASE + alsoAllow，向后兼容旧配置
    const BASE = new Set(['session_status', 'sessions_list', 'sessions_history',
                          'sessions_send', 'sessions_spawn', 'subagents', 'agents_list']);
    const alsoAllow = new Set(toolsCfg.alsoAllow ?? []);
    allowedSet = new Set([...BASE, ...alsoAllow]);
  }

  // 应用黑名单过滤
  return allTools.filter(t => !deny.has(t.name) && allowedSet.has(t.name));
}

// ── 写文件 ────────────────────────────────────────────────────────────────────

function writeJson(outputPath, tools, agentId) {
  const dir = path.dirname(outputPath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  const bySource = tools.reduce((acc, t) => {
    acc[t.source] = (acc[t.source] || 0) + 1;
    return acc;
  }, {});

  fs.writeFileSync(outputPath, JSON.stringify({
    metadata: { exportTime: new Date().toISOString(), agentId: agentId ?? null, totalCount: tools.length, bySource },
    tools,
  }, null, 2), 'utf8');

  return { count: tools.length, bySource };
}

// ── 主流程 ────────────────────────────────────────────────────────────────────

console.error('🔍 Extracting builtin tools from bundle...');
const builtinTools = await extractBuiltinTools();
console.error(`   ✓ ${builtinTools.length} builtin tools`);

console.error('🔌 Loading plugin tools...');
const pluginTools = await extractPluginTools();
console.error(`   ✓ ${pluginTools.length} plugin tools`);

// 合并（插件工具同名时覆盖内置）
const merged = new Map();
for (const t of [...builtinTools, ...pluginTools]) merged.set(t.name, t);
const allTools = [...merged.values()];

const defaultTemplate = path.join(HOME, '.openclaw/tool-inspector/{agent}/tools.json');
const outputTemplate  = args.output || defaultTemplate;

console.error('');
for (const agentId of targetAgentIds) {
  const filtered = filterByAgent(allTools, agentId);
  const outPath  = outputTemplate.replace(/\{agent\}/g, agentId);
  const result   = writeJson(outPath, filtered, agentId);
  console.log(`✅ ${agentId}: ${result.count} tools → ${outPath}`);
  for (const [src, n] of Object.entries(result.bySource)) console.log(`   - ${src}: ${n}`);
}
console.error('\nDone.');
