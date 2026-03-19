#!/usr/bin/env node
/**
 * dump_tools.mjs
 * 从 OpenClaw bundle 提取所有工具的完整 JSON Schema（含 description）
 * 输出到 stdout（JSON 数组），可重定向到文件
 *
 * 用法:
 *   node dump_tools.mjs                          # 输出到 stdout
 *   node dump_tools.mjs > openclaw_all_tools.json
 */

import { existsSync, readFileSync, readdirSync } from 'fs';
import { spawnSync } from 'child_process';
import { createRequire } from 'module';
import path from 'path';

const require = createRequire(import.meta.url);

function resolveExistingPath(candidates) {
  for (const candidate of candidates) {
    if (candidate && existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function extractJsonPayload(rawText) {
  if (!rawText) return null;
  for (let index = 0; index < rawText.length; index += 1) {
    const char = rawText[index];
    if (char !== '{' && char !== '[') continue;
    try {
      return JSON.parse(rawText.slice(index));
    } catch {
    }
  }
  return null;
}

function evalPlainExpression(expr) {
  try {
    return new Function(`return (${expr});`)();
  } catch {
    return null;
  }
}

function skipWhitespace(src, index) {
  let cursor = index;
  while (cursor < src.length && /\s/.test(src[cursor])) cursor += 1;
  return cursor;
}

function parseJsString(src, startIndex) {
  const quote = src[startIndex];
  if (!['"', "'", '`'].includes(quote)) return null;

  let cursor = startIndex + 1;
  let value = '';
  let templateDepth = 0;

  while (cursor < src.length) {
    const char = src[cursor];
    const next = src[cursor + 1];

    if (char === '\\') {
      value += char + (next || '');
      cursor += 2;
      continue;
    }

    if (quote === '`' && char === '$' && next === '{') {
      templateDepth += 1;
      value += '${';
      cursor += 2;
      continue;
    }

    if (quote === '`' && char === '}' && templateDepth > 0) {
      templateDepth -= 1;
      value += char;
      cursor += 1;
      continue;
    }

    if (char === quote && templateDepth === 0) {
      return { value, endIndex: cursor + 1 };
    }

    value += char;
    cursor += 1;
  }

  return null;
}

function parseJsExpression(src, startIndex, stopChars = [';', ',']) {
  let cursor = startIndex;
  let depthParen = 0;
  let depthBrace = 0;
  let depthBracket = 0;
  let activeQuote = null;
  let templateDepth = 0;

  while (cursor < src.length) {
    const char = src[cursor];
    const next = src[cursor + 1];

    if (activeQuote) {
      if (char === '\\') {
        cursor += 2;
        continue;
      }
      if (activeQuote === '`' && char === '$' && next === '{') {
        templateDepth += 1;
        cursor += 2;
        continue;
      }
      if (activeQuote === '`' && char === '}' && templateDepth > 0) {
        templateDepth -= 1;
        cursor += 1;
        continue;
      }
      if (char === activeQuote && templateDepth === 0) {
        activeQuote = null;
      }
      cursor += 1;
      continue;
    }

    if (char === '"' || char === "'" || char === '`') {
      activeQuote = char;
      cursor += 1;
      continue;
    }
    if (char === '(') depthParen += 1;
    else if (char === ')') depthParen -= 1;
    else if (char === '{') depthBrace += 1;
    else if (char === '}') depthBrace -= 1;
    else if (char === '[') depthBracket += 1;
    else if (char === ']') depthBracket -= 1;
    else if (depthParen === 0 && depthBrace === 0 && depthBracket === 0 && stopChars.includes(char)) {
      return { expression: src.slice(startIndex, cursor).trim(), endIndex: cursor };
    }
    cursor += 1;
  }

  return { expression: src.slice(startIndex).trim(), endIndex: src.length };
}

function normalizeDescription(description, maxLines = '2000', maxBytes = '50') {
  return description
    .replace(/\$\{DEFAULT_MAX_LINES\}/g, maxLines)
    .replace(/\$\{DEFAULT_MAX_BYTES\s*\/\s*1024\}/g, maxBytes)
    .replace(/\$\{[^}]+\}/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

// ── 加载 TypeBox ────────────────────────────────────────────────────────────
let Type;
try {
  ({ Type } = require('@sinclair/typebox'));
} catch {
  const candidates = [
    '/opt/homebrew/lib/node_modules/openclaw/node_modules/@sinclair/typebox/build/cjs/index.js',
    '/usr/local/lib/node_modules/openclaw/node_modules/@sinclair/typebox/build/cjs/index.js',
    '/opt/homebrew/lib/node_modules/openclaw/node_modules/@sinclair/typebox/build/cjs/index.js',
    path.join(process.env.HOME, 'node_modules/@sinclair/typebox/build/cjs/index.js'),
  ];
  for (const p of candidates) {
    try { ({ Type } = require(p)); break; } catch {}
  }
}
if (!Type) { console.error('ERROR: @sinclair/typebox not found'); process.exit(1); }

// ── Bundle 路径 ─────────────────────────────────────────────────────────────
const OPENCLAW_DIST = resolveExistingPath([
  process.env.OPENCLAW_DIST,
  '/opt/homebrew/lib/node_modules/openclaw/dist',
  '/usr/local/lib/node_modules/openclaw/dist',
]);

const PI_CODING_TOOLS = resolveExistingPath([
  process.env.PI_CODING_TOOLS,
  '/opt/homebrew/lib/node_modules/openclaw/node_modules/@mariozechner/pi-coding-agent/dist/core/tools',
  '/usr/local/lib/node_modules/openclaw/node_modules/@mariozechner/pi-coding-agent/dist/core/tools',
  path.join(process.env.HOME || '', 'node_modules/@mariozechner/pi-coding-agent/dist/core/tools'),
]);

if (!OPENCLAW_DIST) {
  console.error('ERROR: openclaw dist directory not found');
  process.exit(1);
}

const DIST_FILES = readdirSync(OPENCLAW_DIST).filter(name => name.endsWith('.js'));

function readBundle(fileName) {
  return readFileSync(path.join(OPENCLAW_DIST, fileName), 'utf8');
}

function extractAssignedExpression(src, varName, beforeIndex = src.length) {
  const patterns = [
    `const ${varName} =`,
    `let ${varName} =`,
    `var ${varName} =`,
    `${varName} =`,
  ];

  let bestIndex = -1;
  let bestPattern = null;
  for (const pattern of patterns) {
    const slice = src.slice(0, beforeIndex);
    const index = slice.lastIndexOf(pattern);
    if (index !== -1 && index > bestIndex) {
      bestIndex = index;
      bestPattern = pattern;
    }
  }

  if (bestIndex === -1 || !bestPattern) return null;
  const startIndex = skipWhitespace(src, bestIndex + bestPattern.length);
  return parseJsExpression(src, startIndex, [';']).expression;
}

function resolveSchemaFromExpression(src, expression, beforeIndex = src.length, visited = new Set()) {
  const expr = expression.trim();
  if (!expr) return null;
  if (visited.has(expr)) return null;
  visited.add(expr);

  if (/^[A-Za-z_$][\w$]*$/.test(expr)) {
    const assignedExpression = extractAssignedExpression(src, expr, beforeIndex);
    if (!assignedExpression) return null;
    const resolvedAssigned = resolveSchemaFromExpression(src, assignedExpression, beforeIndex, visited);
    if (resolvedAssigned) return resolvedAssigned;
  }

  const typeboxHelpers = buildTypeboxConstHelpers(src, expr, beforeIndex);
  const typeboxResult = evalTypeboxExpr(expr, typeboxHelpers);
  if (typeboxResult) return typeboxResult;

  const plainResult = evalPlainExpression(expr);
  if (plainResult && typeof plainResult === 'object') return plainResult;

  const schemaIdentifiers = unique(Array.from(expr.matchAll(/([A-Za-z_$][\w$]*Schema)/g)).map(match => match[1]));
  for (const schemaIdentifier of schemaIdentifiers) {
    const resolved = resolveSchemaFromExpression(src, schemaIdentifier, beforeIndex, visited);
    if (resolved) return resolved;
  }

  return null;
}

function resolveDescriptionFromExpression(expression, fallbackName, maxLines = '2000', maxBytes = '50') {
  const expr = expression.trim();
  if (!expr) return `Tool: ${fallbackName}`;

  const firstChar = expr[0];
  if (['"', "'", '`'].includes(firstChar)) {
    const parsed = parseJsString(expr, 0);
    if (parsed) {
      return normalizeDescription(parsed.value, maxLines, maxBytes);
    }
  }

  const evaluated = evalPlainExpression(expr);
  if (typeof evaluated === 'string') {
    return normalizeDescription(evaluated, maxLines, maxBytes);
  }
  if (Array.isArray(evaluated)) {
    return normalizeDescription(evaluated.join(' '), maxLines, maxBytes);
  }

  return `Tool: ${fallbackName}`;
}

function buildTypeboxConstHelpers(src, expr, beforeIndex = src.length) {
  const identifiers = unique(Array.from(expr.matchAll(/\b([A-Z][A-Z0-9_]+)\b/g)).map(match => match[1]));
  const helperLines = [];

  for (const identifier of identifiers) {
    const assignedExpression = extractAssignedExpression(src, identifier, beforeIndex);
    if (!assignedExpression) continue;

    const trimmed = assignedExpression.trim();
    if (/^(\[|\{|"|'|`|-?\d)/.test(trimmed)) {
      helperLines.push(`const ${identifier} = ${trimmed};`);
      continue;
    }

    const plainValue = evalPlainExpression(trimmed);
    if (plainValue !== null && plainValue !== undefined) {
      helperLines.push(`const ${identifier} = ${JSON.stringify(plainValue)};`);
    }
  }

  return helperLines.join('\n');
}
function findBundleByMarkers(markers, preferredPrefixes = []) {
  const candidates = [];
  for (const fileName of DIST_FILES) {
    try {
      const src = readBundle(fileName);
      if (markers.every(marker => src.includes(marker))) {
        const preferredScore = preferredPrefixes.some(prefix => fileName.startsWith(prefix)) ? 1 : 0;
        candidates.push({ fileName, src, preferredScore });
      }
    } catch {
    }
  }

  candidates.sort((left, right) => {
    if (left.preferredScore !== right.preferredScore) return right.preferredScore - left.preferredScore;
    return left.fileName.length - right.fileName.length;
  });

  return candidates[0] || null;
}

const coreBundle = findBundleByMarkers(
  ['execSchema', 'processSchema', 'SessionsListToolSchema', 'SessionsHistoryToolSchema', 'SessionsSendToolSchema'],
  ['reply-', 'agent-', 'model-selection-']
);
const replyBundle = findBundleByMarkers(
  ['MemorySearchSchema', 'MemoryGetSchema', 'WebFetchSchema'],
  ['reply-', 'agent-', 'model-selection-']
);

if (!coreBundle) {
  console.error('ERROR: core tool bundle not found');
  process.exit(1);
}

const piSrc = coreBundle.src;
const replySrc = replyBundle ? replyBundle.src : coreBundle.src;

// ── TypeBox eval helper ─────────────────────────────────────────────────────
function evalTypeboxExpr(expr, extraHelpers = '') {
  const helpers = `
    const optionalStringEnum = (values, opts={}) =>
      Type.Optional(Type.Union(values.map(v => Type.Literal(v)), opts));
    const stringEnum = (values, opts={}) =>
      Type.Union(values.map(v => Type.Literal(v)), opts);
    const EXTRACT_MODES = ["markdown", "text"];
    const CRON_ACTIONS = ["status","list","add","update","remove","run","runs","wake"];
    const CRON_WAKE_MODES = ["now","next-heartbeat"];
    const CRON_RUN_MODES = ["due","force"];
    const REMINDER_CONTEXT_MESSAGES_MAX = 10;
    ${extraHelpers}
  `;
  try {
    return new Function('Type', helpers + `return ${expr}`)(Type);
  } catch(e) {
    return null;
  }
}

function scanToolDefinitionNearIndex(src, sourceLabel, nameMarkerIndex, maxLines = '2000', maxBytes = '50') {
  const nameStart = nameMarkerIndex + 'name: "'.length;
  const nameEnd = src.indexOf('"', nameStart);
  if (nameEnd === -1) return null;

  const name = src.slice(nameStart, nameEnd);
  const descriptionIndex = src.indexOf('description:', nameEnd);
  const parametersIndex = src.indexOf('parameters:', nameEnd);
  const executeIndex = (() => {
    const syncExecute = src.indexOf('execute:', nameEnd);
    const asyncExecute = src.indexOf('async execute(', nameEnd);
    if (syncExecute === -1) return asyncExecute;
    if (asyncExecute === -1) return syncExecute;
    return Math.min(syncExecute, asyncExecute);
  })();

  if (
    descriptionIndex === -1 ||
    parametersIndex === -1 ||
    executeIndex === -1 ||
    !(nameEnd < descriptionIndex && descriptionIndex < parametersIndex && parametersIndex < executeIndex) ||
    executeIndex - nameMarkerIndex > 20000
  ) {
    return null;
  }

  const descriptionValueIndex = skipWhitespace(src, descriptionIndex + 'description:'.length);
  const { expression: descriptionExpression } = parseJsExpression(src, descriptionValueIndex, [',']);

  const parametersValueIndex = skipWhitespace(src, parametersIndex + 'parameters:'.length);
  const { expression: parametersExpression } = parseJsExpression(src, parametersValueIndex, [',']);
  const schema = resolveSchemaFromExpression(src, parametersExpression, parametersIndex);
  if (!schema) return null;

  return {
    type: 'function',
    function: {
      name,
      description: resolveDescriptionFromExpression(descriptionExpression, name, maxLines, maxBytes),
      parameters: schema,
    },
    __source: sourceLabel,
  };
}

function scanToolDefinitionsInSource(src, sourceLabel, maxLines = '2000', maxBytes = '50', targetNames = null) {
  const tools = [];
  const seen = new Set();

  if (targetNames && targetNames.size) {
    for (const name of targetNames) {
      const marker = `name: "${name}"`;
      let cursor = 0;
      while (cursor < src.length) {
        const nameMarkerIndex = src.indexOf(marker, cursor);
        if (nameMarkerIndex === -1) break;
        const tool = scanToolDefinitionNearIndex(src, sourceLabel, nameMarkerIndex, maxLines, maxBytes);
        if (tool && !seen.has(tool.function.name)) {
          tools.push(tool);
          seen.add(tool.function.name);
          break;
        }
        cursor = nameMarkerIndex + marker.length;
      }
    }
    return tools;
  }

  let cursor = 0;

  while (cursor < src.length) {
    const nameMarkerIndex = src.indexOf('name: "', cursor);
    if (nameMarkerIndex === -1) break;
    const tool = scanToolDefinitionNearIndex(src, sourceLabel, nameMarkerIndex, maxLines, maxBytes);
    if (tool && !seen.has(tool.function.name)) {
      tools.push(tool);
      seen.add(tool.function.name);
    }
    cursor = nameMarkerIndex + 'name: "'.length;
  }

  return tools;
}

// ── image: 内联在 createImageTool 里 ──────────────────────────────────────
function extractImage(src) {
  const pos = src.indexOf('name: "image"');
  if (pos === -1) return null;
  const chunk = src.slice(pos, pos + 3000);

  // description 就在 name: "image" 后面
  const dm = chunk.match(/name:\s*"image",\s*\n?\s*(?:label:[^\n]+\n\s*)?description:\s*"([^"]{20,400})"/);
  const description = dm ? dm[1] : 'Analyze one or more images with a vision model.';

  const paramPos = chunk.indexOf('parameters: Type.Object(');
  if (paramPos === -1) return null;
  const schema = resolveSchemaFromExpression(chunk, 'Type.Object(' + chunk.slice(paramPos + 'parameters: '.length).split('Type.Object(')[1]);
  if (!schema) return null;
  return { description, schema };
}

// ── memory: 在 reply bundle 或 memory plugin ───────────────────────────────
function extractMemoryTools(src) {
  const result = {};

  // memory_search
  const msPos = src.indexOf('name: "memory_search"');
  if (msPos > -1) {
    const chunk = src.slice(msPos, msPos + 3000);
    const paramPos = chunk.indexOf('parameters: Type.Object(');
    if (paramPos > -1) {
      const schema = resolveSchemaFromExpression(chunk, 'Type.Object(' + chunk.slice(paramPos + 'parameters: '.length).split('Type.Object(')[1]);
      if (schema) {
        const dm = chunk.match(/description:\s*"([^"]{20,400})"/);
        result.memory_search = { description: dm ? dm[1] : 'Semantically search memory files.', schema };
      }
    }
  }

  // memory_get
  const mgPos = src.indexOf('name: "memory_get"');
  if (mgPos > -1) {
    const chunk = src.slice(mgPos, mgPos + 3000);
    const paramPos = chunk.indexOf('parameters: Type.Object(');
    if (paramPos > -1) {
      const schema = resolveSchemaFromExpression(chunk, 'Type.Object(' + chunk.slice(paramPos + 'parameters: '.length).split('Type.Object(')[1]);
      if (schema) {
        const dm = chunk.match(/description:\s*"([^"]{20,300})"/);
        result.memory_get = { description: dm ? dm[1] : 'Safe snippet read from memory files.', schema };
      }
    }
  }

  return result;
}

// ── 提取 sessions_spawn（含 optionalStringEnum） ───────────────────────────
function extractSessionsSpawn() {
  // 先拿到 SESSIONS_SPAWN_RUNTIMES 的值
  const rrMatch = piSrc.match(/SESSIONS_SPAWN_RUNTIMES\s*=\s*(\[[^\]]+\])/);
  const runtimes = rrMatch ? JSON.parse(rrMatch[1]) : ['subagent', 'acp'];

  const smMatch = piSrc.match(/SESSIONS_SPAWN_SANDBOX_MODES\s*=\s*(\[[^\]]+\])/);
  const sandboxModes = smMatch ? JSON.parse(smMatch[1]) : ['inherit', 'require'];

  const modesMatch = piSrc.match(/SESSIONS_SPAWN_MODES\s*=\s*(\[[^\]]+\])/);
  const modes = modesMatch ? JSON.parse(modesMatch[1]) : ['run', 'session'];

  const cleanupMatch = piSrc.match(/SESSIONS_SPAWN_CLEANUP\s*=\s*(\[[^\]]+\])/);
  const cleanup = cleanupMatch ? JSON.parse(cleanupMatch[1]) : ['delete', 'keep'];

  const streamToMatch = piSrc.match(/SESSIONS_SPAWN_STREAM_TO\s*=\s*(\[[^\]]+\])/);
  const streamTo = streamToMatch ? JSON.parse(streamToMatch[1]) : ['parent'];

  // 手动构建（避免 optionalStringEnum 依赖问题）
  const schema = Type.Object({
    task: Type.String(),
    label: Type.Optional(Type.String({ maxLength: 64, minLength: 1 })),
    runtime: Type.Optional(Type.Union(runtimes.map(v => Type.Literal(v)))),
    agentId: Type.Optional(Type.String()),
    resumeSessionId: Type.Optional(Type.String({
      description: 'Resume an existing agent session by its ID (e.g. a Codex session UUID from ~/.codex/sessions/). Requires runtime="acp". The agent replays conversation history via session/load instead of starting fresh.'
    })),
    model: Type.Optional(Type.String()),
    thinking: Type.Optional(Type.String()),
    cwd: Type.Optional(Type.String()),
    runTimeoutSeconds: Type.Optional(Type.Number({ minimum: 0 })),
    timeoutSeconds: Type.Optional(Type.Number({ minimum: 0 })),
    thread: Type.Optional(Type.Boolean()),
    mode: Type.Optional(Type.Union(modes.map(v => Type.Literal(v)))),
    cleanup: Type.Optional(Type.Union(cleanup.map(v => Type.Literal(v)))),
    sandbox: Type.Optional(Type.Union(sandboxModes.map(v => Type.Literal(v)))),
    streamTo: Type.Optional(Type.Union(streamTo.map(v => Type.Literal(v)))),
  });

  return {
    description: 'Spawn an isolated session (runtime="subagent" or runtime="acp"). mode="run" is one-shot and mode="session" is persistent/thread-bound. Subagents inherit the parent workspace directory automatically.',
    schema
  };
}

function resolvePreferredAgent() {
  if (process.env.OPENCLAW_TOOLS_AGENT) return process.env.OPENCLAW_TOOLS_AGENT;
  const workerSessionStore = path.join(process.env.HOME || '', '.openclaw/agents/gendata-worker-1/sessions/sessions.json');
  if (existsSync(workerSessionStore)) return 'gendata-worker-1';
  return 'main';
}

function discoverPluginSourceFiles() {
  const extensionsDir = path.join(process.env.HOME || '', '.openclaw/extensions');
  if (!existsSync(extensionsDir)) return [];

  const pluginFiles = [];
  for (const pluginName of readdirSync(extensionsDir)) {
    for (const candidate of ['index.ts', 'index.js']) {
      const filePath = path.join(extensionsDir, pluginName, candidate);
      if (existsSync(filePath)) pluginFiles.push(filePath);
    }
  }
  return pluginFiles;
}

function readCurrentToolNamesFromSessionStore(agentId) {
  const sessionStorePath = path.join(process.env.HOME || '', `.openclaw/agents/${agentId}/sessions/sessions.json`);
  if (!existsSync(sessionStorePath)) return [];

  try {
    const sessionStore = JSON.parse(readFileSync(sessionStorePath, 'utf8'));
    const sessionInfo = sessionStore[`agent:${agentId}:main`];
    const entries = sessionInfo?.systemPromptReport?.tools?.entries || [];
    return unique(entries.map(entry => entry?.name));
  } catch {
    return [];
  }
}

function probeCurrentToolNames(agentId) {
  const result = spawnSync(
    'openclaw',
    ['agent', '--agent', agentId, '--message', '请只回复 OK', '--json'],
    { encoding: 'utf8' }
  );

  if (result.status !== 0) {
    throw new Error(result.stderr || `openclaw agent probe failed for ${agentId}`);
  }

  const payload = extractJsonPayload(result.stdout || '');
  const entries = payload?.result?.meta?.systemPromptReport?.tools?.entries || [];
  return unique(entries.map(entry => entry?.name));
}

function discoverCurrentToolNames() {
  const agentId = resolvePreferredAgent();
  const sessionToolNames = readCurrentToolNamesFromSessionStore(agentId);
  if (sessionToolNames.length) {
    process.stderr.write(`ℹ️  using tool names from session store for agent ${agentId}\n`);
    return sessionToolNames;
  }

  process.stderr.write(`ℹ️  probing current tool names from agent ${agentId}\n`);
  return probeCurrentToolNames(agentId);
}

// ── 提取 web_search ────────────────────────────────────────────────────────
function extractWebSearch() {
  if (!replySrc) return null;
  // 直接手动构建（createWebSearchSchema 依赖运行时 provider config）
  const schema = Type.Object({
    query: Type.String({ description: 'Search query string.' }),
    count: Type.Optional(Type.Number({ description: 'Number of results to return (1-10).', minimum: 1, maximum: 10 })),
    country: Type.Optional(Type.String({ description: "2-letter country code for region-specific results (e.g., 'DE', 'US', 'ALL'). Default: 'US'." })),
    language: Type.Optional(Type.String({ description: "ISO 639-1 language code for results (e.g., 'en', 'de', 'fr')." })),
    freshness: Type.Optional(Type.String({ description: "Filter by time: 'day' (24h), 'week', 'month', or 'year'." })),
    date_after: Type.Optional(Type.String({ description: 'Only results published after this date (YYYY-MM-DD).' })),
    date_before: Type.Optional(Type.String({ description: 'Only results published before this date (YYYY-MM-DD).' })),
    search_lang: Type.Optional(Type.String({ description: "Brave language code for search results (e.g., 'en', 'de', 'en-gb')." })),
    ui_lang: Type.Optional(Type.String({ description: "Locale code for UI elements (e.g., 'en-US', 'de-DE'). Must include region subtag." })),
  });
  return {
    description: 'Search the web using Brave Search API. Supports region-specific and localized search via country and language parameters. Returns titles, URLs, and snippets for fast research.',
    schema
  };
}

function extractDynamicToolDefinitions() {
  const truncateSrc = PI_CODING_TOOLS && existsSync(path.join(PI_CODING_TOOLS, 'truncate.js'))
    ? readFileSync(path.join(PI_CODING_TOOLS, 'truncate.js'), 'utf8')
    : '';
  const maxLines = (truncateSrc.match(/DEFAULT_MAX_LINES\s*=\s*(\d+)/) || [])[1] || '2000';
  const maxBytes = (truncateSrc.match(/DEFAULT_MAX_BYTES\s*=\s*(\d+)/) || [])[1] || '50';

  const currentToolNames = discoverCurrentToolNames();
  const targetNames = currentToolNames.length ? new Set(currentToolNames) : null;

  const discovered = [];
  discovered.push(...scanToolDefinitionsInSource(piSrc, coreBundle.fileName, maxLines, maxBytes, targetNames));
  if (replyBundle && replyBundle.fileName !== coreBundle.fileName) {
    discovered.push(...scanToolDefinitionsInSource(replySrc, replyBundle.fileName, maxLines, maxBytes, targetNames));
  }

  if (PI_CODING_TOOLS) {
    for (const fileName of readdirSync(PI_CODING_TOOLS).filter(name => name.endsWith('.js'))) {
      const src = readFileSync(path.join(PI_CODING_TOOLS, fileName), 'utf8');
      discovered.push(...scanToolDefinitionsInSource(src, fileName, maxLines, maxBytes, targetNames));
    }
  }

  for (const pluginSourceFile of discoverPluginSourceFiles()) {
    const src = readFileSync(pluginSourceFile, 'utf8');
    discovered.push(...scanToolDefinitionsInSource(src, path.basename(pluginSourceFile), maxLines, maxBytes, targetNames));
  }

  const byName = new Map();
  for (const tool of discovered) {
    const name = tool.function?.name;
    if (name && !byName.has(name)) {
      byName.set(name, tool);
    }
  }

  const output = [];
  for (const name of currentToolNames.length ? currentToolNames : byName.keys()) {
    if (byName.has(name)) {
      output.push(byName.get(name));
      process.stderr.write(`✅ ${name}\n`);
      continue;
    }

    if (name === 'web_search') {
      const result = extractWebSearch();
      if (result) {
        output.push({ type: 'function', function: { name, description: result.description, parameters: result.schema } });
        process.stderr.write(`✅ ${name} (fallback)\n`);
        continue;
      }
    }

    process.stderr.write(`⚠️  ${name}: definition not resolved\n`);
  }

  return output;
}

// ── 主流程 ─────────────────────────────────────────────────────────────────
const output = extractDynamicToolDefinitions();

process.stderr.write(`\nTotal: ${output.length} tools\n`);
process.stdout.write(JSON.stringify(output, null, 2) + '\n');
