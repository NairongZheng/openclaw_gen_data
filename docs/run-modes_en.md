# Run Modes

This document specifically explains the three main run modes currently supported by the project, as well as how the input files should be provided.

## 1. Plain intent mode

Suitable for traditional user intent data.

Place the input file at `paths.intents_file`. Each line contains at least:

```jsonl
{"id": "intent_1", "natural_language_intent": "Help me summarize the latest AI news"}
```

At runtime it enters the existing user loop:

1. The LLM generates the next query
2. OpenClaw executes one round of interaction
3. Repeat until the task is complete or `generation.max_turns` is reached

Example:

```bash
INTENTS_FILE=data/intents.jsonl \
python scripts/run_generation.py --concurrent 4
```

## 2. Query-only mode

Suitable for search Q&A and query-only data, where there is no need to run an intent first.

In this case `paths.intents_file` is still used, but its content is changed to direct query data. Two common formats are supported:

```jsonl
{"id": "query_1", "query": "Which AI Agent products are worth paying attention to in 2025?"}
{"id": "query_2", "question": "Which are the strongest multimodal models in 2025?", "answer": "Optional reference answer"}
```

Run behavior:

- `query` / `question` will be normalized into `direct_query`
- No longer goes through the LLM user loop
- The query is sent directly to OpenClaw
- The session is still archived, and the middle format is still converted

Example:

```bash
INTENTS_FILE=data/merged_data_sample_20.jsonl \
APPEND_QUERY_ENABLED=false \
python scripts/run_generation.py --concurrent 4
```

Notes:

- In query-only mode, `generation.intents_per_session` is still effective
- It controls "how many queries are grouped into the same session before finalizing"
- It is generally recommended to set `APPEND_QUERY_ENABLED=false` to avoid appending an extra query after the query task finishes

## 3. intent + finalize-time appended query mode

Suitable for the scenario of "first run a batch of plain intents, then add a search query before the session finalizes normally".

In this case two input files are required:

- `paths.intents_file`: the main intent file
- `generation.append_query_file`: the query pool file

Configuration example:

```yaml
generation:
  intents_per_session: 3
  append_query_enabled: true
  append_query_file: "data/merged_data_sample_20.jsonl"

paths:
  intents_file: "data/intents.jsonl"
```

Corresponding command:

```bash
INTENTS_FILE=data/intents.jsonl \
INTENTS_PER_SESSION=3 \
APPEND_QUERY_ENABLED=true \
APPEND_QUERY_FILE=data/merged_data_sample_20.jsonl \
python scripts/run_generation.py --concurrent 4
```

Behavior notes:

- Only enabled when `append_query_enabled=true` and `append_query_file` is non-empty
- As long as the current session finalizes normally, exactly 1 query is appended before finalize
- The appended query comes from `append_query_file`, with the `direct_query` records filtered out first
- The appended query is written into the same session trajectory and batch metadata
- The appended query does not add a separate progress item

## Automatic normalization rules for input records

The main pipeline normalizes the input uniformly into a task:

- Has `natural_language_intent`: treated as `intent`
- Has `query` or `question`, but no `natural_language_intent`: treated as `direct_query`
- If there is no explicit `id`, a stable id is automatically generated
- If there is an `answer` field, it is written into `metadata.reference_answer`

## How to choose

- Only intent data: use plain intent mode
- Only search query data: use query-only mode
- Want to add a query when each session finalizes: use intent + finalize-time appended query mode
