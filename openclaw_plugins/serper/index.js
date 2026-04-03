import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import {
  DEFAULT_CACHE_TTL_MINUTES,
  enablePluginInConfig,
  getScopedCredentialValue,
  normalizeCacheKey,
  readCache,
  resolveCacheTtlMs,
  resolveProviderWebSearchPluginConfig,
  resolveSearchCount,
  resolveSiteName,
  resolveTimeoutSeconds,
  setProviderWebSearchPluginConfigValue,
  setScopedCredentialValue,
  withTrustedWebSearchEndpoint,
  wrapWebContent,
  writeCache,
} from "openclaw/plugin-sdk/provider-web-search";

const DEFAULT_SERPER_BASE_URL = "https://google.serper.dev";
const DEFAULT_SEARCH_TIMEOUT_SECONDS = 30;
const DEFAULT_SEARCH_COUNT = 5;
const MAX_SEARCH_COUNT = 10;

const SEARCH_CACHE = new Map();

const GenericSerperSearchSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    query: {
      type: "string",
      description: "Search query string.",
    },
    count: {
      type: "number",
      minimum: 1,
      maximum: MAX_SEARCH_COUNT,
      description: `Number of results to return (1-${MAX_SEARCH_COUNT}).`,
    },
  },
  required: ["query"],
};

function normalizeSecretString(value) {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function resolveLegacySerperConfig(cfg) {
  const search = cfg?.tools?.web?.search;
  if (!search || typeof search !== "object" || Array.isArray(search)) {
    return undefined;
  }
  const legacy = search.serper;
  if (!legacy || typeof legacy !== "object" || Array.isArray(legacy)) {
    return undefined;
  }
  return legacy;
}

function resolveSerperConfig(cfg) {
  const pluginConfig = cfg?.plugins?.entries?.serper?.config?.webSearch;
  if (pluginConfig && typeof pluginConfig === "object" && !Array.isArray(pluginConfig)) {
    return pluginConfig;
  }
  return resolveLegacySerperConfig(cfg);
}

function resolveSerperApiKey(cfg) {
  const searchConfig = resolveSerperConfig(cfg);
  return (
    normalizeSecretString(searchConfig?.apiKey) ||
    normalizeSecretString(process.env.SERPER_API_KEY) ||
    normalizeSecretString(process.env.OPENCLAW_SEARCH_API_KEY)
  );
}

function resolveSerperBaseUrl(cfg) {
  const searchConfig = resolveSerperConfig(cfg);
  return (
    normalizeSecretString(searchConfig?.baseUrl) ||
    normalizeSecretString(process.env.SERPER_BASE_URL) ||
    normalizeSecretString(process.env.OPENCLAW_SEARCH_BASE_URL) ||
    DEFAULT_SERPER_BASE_URL
  );
}

function resolveSerperTimeoutSeconds(cfg, override) {
  if (typeof override === "number" && Number.isFinite(override) && override > 0) {
    return Math.floor(override);
  }
  const searchConfig = resolveSerperConfig(cfg);
  return resolveTimeoutSeconds(searchConfig?.timeoutSeconds, DEFAULT_SEARCH_TIMEOUT_SECONDS);
}

function resolveEndpoint(baseUrl) {
  const url = new URL(baseUrl);
  url.pathname = url.pathname.endsWith("/search")
    ? url.pathname
    : `${url.pathname.replace(/\/$/, "")}/search`;
  return url.toString();
}

function readTextField(record, fieldNames) {
  for (const fieldName of fieldNames) {
    const value = record?.[fieldName];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function buildAnswerBoxResult(answerBox) {
  if (!answerBox || typeof answerBox !== "object") {
    return null;
  }
  const url = readTextField(answerBox, ["link", "url"]);
  if (!url) {
    return null;
  }
  const title = readTextField(answerBox, ["title", "answer", "snippet"]);
  const snippet = readTextField(answerBox, ["snippet", "answer"]);
  return {
    title: title || url,
    url,
    snippet,
    siteName: resolveSiteName(url) || undefined,
  };
}

function buildKnowledgeGraphResult(knowledgeGraph) {
  if (!knowledgeGraph || typeof knowledgeGraph !== "object") {
    return null;
  }
  const url = readTextField(knowledgeGraph, ["website", "url"]);
  if (!url) {
    return null;
  }
  const title = readTextField(knowledgeGraph, ["title", "name"]);
  const snippet = readTextField(knowledgeGraph, ["description"]);
  return {
    title: title || url,
    url,
    snippet,
    siteName: resolveSiteName(url) || undefined,
  };
}

function buildOrganicResults(payload, count) {
  const candidates = [];
  const answerBoxResult = buildAnswerBoxResult(payload?.answerBox);
  if (answerBoxResult) {
    candidates.push(answerBoxResult);
  }
  const knowledgeGraphResult = buildKnowledgeGraphResult(payload?.knowledgeGraph);
  if (knowledgeGraphResult) {
    candidates.push(knowledgeGraphResult);
  }

  const organic = Array.isArray(payload?.organic) ? payload.organic : [];
  for (const entry of organic) {
    if (!entry || typeof entry !== "object") {
      continue;
    }
    const url = readTextField(entry, ["link", "url"]);
    if (!url) {
      continue;
    }
    candidates.push({
      title: readTextField(entry, ["title"]) || url,
      url,
      snippet: readTextField(entry, ["snippet"]),
      siteName: resolveSiteName(url) || undefined,
      position:
        typeof entry.position === "number" && Number.isFinite(entry.position)
          ? entry.position
          : undefined,
      date: readTextField(entry, ["date"]) || undefined,
    });
    if (candidates.length >= count) {
      break;
    }
  }

  const deduped = [];
  const seen = new Set();
  for (const result of candidates) {
    if (!result.url || seen.has(result.url)) {
      continue;
    }
    seen.add(result.url);
    deduped.push(result);
    if (deduped.length >= count) {
      break;
    }
  }
  return deduped;
}

async function runSerperSearch(params) {
  const apiKey = resolveSerperApiKey(params.cfg);
  if (!apiKey) {
    throw new Error(
      "web_search (serper) needs a Serper API key. Set SERPER_API_KEY / OPENCLAW_SEARCH_API_KEY in the Gateway environment, or configure plugins.entries.serper.config.webSearch.apiKey.",
    );
  }

  const count = resolveSearchCount(params.count, DEFAULT_SEARCH_COUNT);
  const normalizedCount = Math.max(1, Math.min(MAX_SEARCH_COUNT, count));
  const baseUrl = resolveSerperBaseUrl(params.cfg);
  const timeoutSeconds = resolveSerperTimeoutSeconds(params.cfg, params.timeoutSeconds);
  const cacheTtlMs = resolveCacheTtlMs(params.cacheTtlMinutes, DEFAULT_CACHE_TTL_MINUTES);

  const cacheKey = normalizeCacheKey(
    JSON.stringify({
      provider: "serper",
      query: params.query,
      count: normalizedCount,
      baseUrl,
      timeoutSeconds,
    }),
  );
  const cached = readCache(SEARCH_CACHE, cacheKey);
  if (cached) {
    return { ...cached.value, cached: true };
  }

  const startedAt = Date.now();
  const payload = await withTrustedWebSearchEndpoint(
    {
      url: resolveEndpoint(baseUrl),
      timeoutSeconds,
      init: {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-API-KEY": apiKey,
        },
        body: JSON.stringify({
          q: params.query,
          num: normalizedCount,
        }),
      },
    },
    async (response) => {
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(
          `Serper Search API error (${response.status}): ${detail || response.statusText}`,
        );
      }
      return await response.json();
    },
  );

  const results = buildOrganicResults(payload, normalizedCount).map((entry) => ({
    title: entry.title ? wrapWebContent(entry.title, "web_search") : "",
    url: entry.url,
    snippet: entry.snippet ? wrapWebContent(entry.snippet, "web_search") : "",
    ...(entry.siteName ? { siteName: entry.siteName } : {}),
    ...(entry.position !== undefined ? { position: entry.position } : {}),
    ...(entry.date ? { published: entry.date } : {}),
  }));

  const result = {
    query: params.query,
    provider: "serper",
    count: results.length,
    tookMs: Date.now() - startedAt,
    externalContent: {
      untrusted: true,
      source: "web_search",
      provider: "serper",
      wrapped: true,
    },
    results,
  };

  writeCache(SEARCH_CACHE, cacheKey, result, cacheTtlMs);
  return result;
}

function createSerperWebSearchProvider() {
  return {
    id: "serper",
    label: "Serper Search",
    hint: "Google-backed structured web search via Serper",
    onboardingScopes: ["text-inference"],
    credentialLabel: "Serper API key",
    envVars: ["SERPER_API_KEY", "OPENCLAW_SEARCH_API_KEY"],
    placeholder: "serper_...",
    signupUrl: "https://serper.dev/",
    docsUrl: "https://serper.dev/",
    autoDetectOrder: 65,
    credentialPath: "plugins.entries.serper.config.webSearch.apiKey",
    inactiveSecretPaths: ["plugins.entries.serper.config.webSearch.apiKey"],
    getCredentialValue: (searchConfig) => getScopedCredentialValue(searchConfig, "serper"),
    setCredentialValue: (searchConfigTarget, value) =>
      setScopedCredentialValue(searchConfigTarget, "serper", value),
    getConfiguredCredentialValue: (config) =>
      resolveProviderWebSearchPluginConfig(config, "serper")?.apiKey,
    setConfiguredCredentialValue: (configTarget, value) => {
      setProviderWebSearchPluginConfigValue(configTarget, "serper", "apiKey", value);
    },
    applySelectionConfig: (config) => enablePluginInConfig(config, "serper").config,
    createTool: (ctx) => ({
      description:
        "Search the web using Serper. Returns titles, URLs, and snippets from Serper Search.",
      parameters: GenericSerperSearchSchema,
      execute: async (args) =>
        await runSerperSearch({
          cfg: ctx.config,
          query: typeof args.query === "string" ? args.query : "",
          count: typeof args.count === "number" ? args.count : undefined,
        }),
    }),
  };
}

export default definePluginEntry({
  id: "serper",
  name: "Serper Plugin",
  description: "Serper-backed web search provider for OpenClaw",
  register(api) {
    api.registerWebSearchProvider(createSerperWebSearchProvider());
  },
});
