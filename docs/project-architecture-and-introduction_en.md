# OpenClaw Gen Data Project Deep Dive

## 1. Project Background

`openclaw_gen_data` is a data generation engineering project built around OpenClaw. Its goal is to engineer, automate, and make recoverable the pipeline of "user intent → multi-turn Agent interaction → raw session trajectory → training-ready intermediate-format data."

The direct motivation of this project is not "calling an Agent once to complete a task," but rather to **construct high-quality trajectory data in bulk**, serving downstream large-model training, replay analysis, behavior auditing, and dataset building.

In many Agent training scenarios, the most valuable data is not single-turn Q&A, but rather:

- How a user intent is decomposed into multi-turn queries
- How the Agent invokes tools and handles intermediate results
- Why it stops asking follow-up questions at a certain moment
- How a task trajectory is ultimately turned into a structured training sample

This project is designed precisely for this goal:

1. Read a large number of tasks from JSONL files (supporting both intent and query)
2. Drive local OpenClaw worker agents and the tool system to complete real interactions
3. Archive the complete session as a raw trajectory
4. Convert the archived trajectory into a middle format suitable for training
5. Stay stable under complex scenarios such as concurrency, retries, recovery, configuration drift, and session resume

From an engineering positioning standpoint, it is not a general-purpose chatbot project, but rather a **batch-processing system oriented toward Agent trajectory collection and training-data construction**.

---

## 2. Core Project Goals

The problems this project solves can be summarized into four categories:

### 2.1 Data Generation Automation

Given a batch of input tasks, the system can automatically complete:

- Task reading
- Agent initialization
- query generation
- OpenClaw interaction
- session archiving
- middle format conversion
- Progress recording and summary output

### 2.2 Real Run Trajectory Fidelity

This project tries to avoid generating only "simulated data," and instead emphasizes:

- Really using the OpenClaw agent
- Really triggering the OpenClaw tool system
- Really preserving tool calls and tool results
- Really parsing the message sequence from the session
- Really capturing the tool definitions ultimately sent to the model in the runtime

### 2.3 Batch Concurrency and Resume

Real data generation is often large in scale, runs for a long time, and may encounter the following midway:

- OpenClaw service instability
- Configuration file contamination
- worker failures midway
- The need to resume after the program is interrupted
- Loss of intermediate state when multiple intents share one session

Therefore, the project's design emphasizes:

- Multi-worker concurrency
- Progress-file resume
- worker runtime snapshot
- Deferred session finalization
- OpenClaw runtime self-healing and automatic restart

### 2.4 Output Trainable Intermediate Format

Although the raw session contains complete information, it is not suitable to be fed directly into the training pipeline. Therefore, the project provides a dedicated converter that transforms the trajectory into JSON with a clearer structure:

- `messages`
- `tools`
- `skills`
- `final_output`
- `metadata`

This ultimately forms a unified middle format, convenient for downstream SFT / analysis / replay.

---

## 3. Tech Stack and Dependency Form

The project is currently based mainly on the following tech stack:

### 3.1 Python Main Engineering

The core logic is essentially all implemented in Python, including:

- Configuration loading
- Task reading and normalization
- OpenClaw CLI wrapping
- LLM user loop
- session parsing
- middle format conversion
- runtime recovery
- worker snapshot
- agent initialization and runtime metadata refresh

### 3.2 OpenClaw Local Runtime

The project strongly depends on the local OpenClaw environment. What OpenClaw is responsible for here is:

- agent management
- workspace isolation
- session persistence
- Tool system operation
- gateway model access

This project does not rewrite OpenClaw, but rather orchestrates it through the CLI and configuration files.

### 3.3 OpenAI-Compatible LLM API

There are two kinds of model calls in the project:

1. **OpenClaw underlying model**: used for the OpenClaw agent itself to execute tasks
2. **External LLMClient**: used for the user loop to decide "what the next query is / whether it is complete"

This gives the system a natural "dual-model separation of responsibilities" characteristic:

- OpenClaw is responsible for actual agent execution
- LLMClient is responsible for high-level query scheduling and completion determination

### 3.4 Node.js Auxiliary Pipeline

Although the main pipeline has now switched to runtime probe to capture tools, the repository still retains `dump_tools.mjs` as a static scanning and offline comparison tool.

### 3.5 Docker / GitHub Actions

The project provides:

- Containerized run entry point
- Dockerfile
- CI image build workflow
- Multi-architecture image build and on-demand publishing

This means the project can not only run locally, but also has deployability and portability.

---

## 4. Overall Project Architecture

Logically, this project can be broken down into 6 layers:

### 4.1 Input Layer

Responsible for receiving and normalizing task input.

Core file:

- `src/intent_loader.py`

Supports two kinds of tasks:

- `intent`: centered on `natural_language_intent`
- `direct_query`: centered on `query` or `question`

After normalization, the following is uniformly obtained:

- `id`
- `task_type`
- `natural_language_intent`
- `query` (if applicable)
- `metadata`

### 4.2 Scheduling Layer

Responsible for orchestrating the global run flow.

Core files:

- `scripts/run_generation.py`
- `src/generation_support.py`

Responsibilities include:

- Reading configuration
- Initializing worker agents
- Loading shared runtime metadata
- Building the task queue
- Starting multi-threaded workers
- Aggregating results
- Triggering runtime recovery

### 4.3 Decision Layer (User Loop)

Responsible for deciding what query to send to OpenClaw each turn.

Core files:

- `src/llm_client.py`
- `prompts/user_model_system_prompt.txt`

The logic is:

- Input the raw user intent
- Input the persona
- Input the conversation history
- Have an external LLM generate:
  - Whether the task is already complete
  - If not complete, what the next best query is

This layer is effectively the logical core of "simulating the user driving the task forward."

### 4.4 Execution Layer (OpenClaw Runtime)

Responsible for actually interacting with the OpenClaw agent.

Core files:

- `src/openclaw_wrapper.py`
- `scripts/init_agents.py`

Responsibilities include:

- agent creation and configuration
- workspace/state path management
- Global provider configuration
- Global skills configuration
- session reset, archive, restore
- worker workspace template cloning
- runtime tools probe

### 4.5 Recovery Layer

Responsible for preserving run progress and environment stability under abnormal conditions.

Core files:

- `src/worker_snapshot.py`
- `src/agent_runtime.py`
- `src/runtime_recovery.py`
- `src/fs_utils.py`

Capabilities include:

- worker-level runtime snapshot
- pending session recovery
- OpenClaw configuration baseline rollback
- gateway restart
- Read-only file permission repair and safe deletion

### 4.6 Conversion Layer

Responsible for converting the raw session into a training-ready middle format.

Core files:

- `src/session_parser.py`
- `src/converter.py`

Here the following is accomplished:

- JSONL session parsing
- assistant / user / toolResult extraction
- tool_calls and tool_results alignment
- reasoning content extraction
- system prompt injection
- tools / skills / metadata aggregation

---

## 5. Run Pipeline Deep Dive

The following explains by following the lifecycle of one complete task generation.

### 5.1 Initialization Phase

Entry point: `scripts/init_agents.py`

The initialization actions include:

1. Ensure worker agents exist according to the configuration
2. If needed, delete old agents and rebuild
3. Allocate an independent workspace for each agent
4. Configure the global provider and global skills
5. Generate a shared workspace snapshot
6. Optionally refresh the shared runtime metadata (`--refresh-tools`)

There is a very important engineering point here:

- workers are not configured manually one by one each time, but managed uniformly
- A newly created agent can reuse the template workspace
- The initialization result affects the stability of the entire downstream data generation flow

### 5.2 runtime metadata Refresh Phase

When executing:

```bash
python scripts/init_agents.py --refresh-tools
```

The current main pipeline no longer relies on pure static scanning, but instead:

1. Starts a short-lived local proxy
2. Creates a temporary probe agent
3. Issues one minimal real request
4. Captures the shared runtime metadata (`tools` + `system_prompt`) that OpenClaw ultimately sends out to the model
5. Writes the result to `output/worker_snapshots/runtime_metadata/runtime_metadata.json`
6. Simultaneously writes `runtime_probe_*` debug snapshots to `output/worker_snapshots/runtime_metadata/probe/`

Corresponding files:

- `scripts/init_agents.py`
- `src/runtime_tools_proxy.py`
- `tools/tool-inspector/README.md`

This is a very important recent evolution of the project, because it solved the inconsistency problem between "statically extracted tool definitions" and "real runtime tool definitions."

### 5.3 generation Main Loop

Entry point: `scripts/run_generation.py`

The main flow is roughly as follows:

1. Read configuration
2. Prepare output directory
3. Ensure worker agents exist
4. Back up the OpenClaw runtime baseline
5. Read tasks
6. Construct the task queue
7. Start multiple workers to execute concurrently
8. Aggregate results and summary
9. Clean up agents on normal exit

### 5.4 How a Single worker Processes

A worker is driven by `worker_loop()`, and its behavior is:

- A single worker consumes tasks serially internally
- Multiple workers run concurrently with each other
- Each worker is bound to a fixed agent
- Each worker can process multiple intents consecutively, and decides when to finalize the session according to `intents_per_session`

The significance of this design is:

- Concurrency is simple and stable
- session state and workspace state are easier to manage per worker
- snapshot recovery is also easier to implement

### 5.5 How a Single task Is Processed

Core function: `process_intent()`

When the task is `intent`:

1. Initialize the conversation history
2. Call `LLMClient.generate_next_query()`
3. If the LLM determines completion, stop
4. If not complete, obtain the next query
5. Send the query to OpenClaw
6. Extract the assistant text from the OpenClaw response
7. Update the history
8. Repeat until complete or the maximum number of turns is reached

When the task is `direct_query`:

- Does not go through the user loop
- Sends the query directly to OpenClaw
- Archives the session upon success

### 5.6 session Finalization and Deferred Materialization

A very interesting design is: **the session can be finalized lazily**.

If `intents_per_session > 1`, then:

- The results of the first several intents are first held in `pending_session_results`
- The worker runtime snapshot is saved at the same time
- Only when the last intent completes is the session actually archived and the middle format converted

Advantages:

- Keeps the real trajectory of multiple consecutive intents within one session
- Makes "single-session multi-task" training data possible
- Reduces the overhead of resetting workspace/session for every task

### 5.7 Conversion to middle format

After the session is finalized, `DataConverter` performs the conversion.

The output structure contains:

- `status`
- `session_id`
- `source_intent_ids`
- `messages`
- `tools`
- `skills`
- `final_output`
- `metadata`

The most critical among them is `messages`:

- user messages come from the session's raw message
- assistant messages preserve text, tool_calls, reasoning_content
- tool messages preserve the tool name, tool_call_id, content, success

This makes the middle format close to the OpenAI-style message structure, while also preserving project-specific metadata.

---

## 6. Core Module Descriptions

## 6.1 `src/intent_loader.py`

Role: unify the input task format.

Highlights:

- Supports both `intent` and `direct_query`
- Automatically generates stable IDs
- Automatically normalizes `question/answer` data
- Skips and warns about invalid JSONL records

This is a "fault-tolerant adaptation layer" at the data entry point.

## 6.2 `src/llm_client.py`

Role: implement the high-level query generator of the user loop.

Features:

- Supports the differences in thinking parameters across different model families
- Uses a unified JSON response format
- Built-in exponential backoff retry
- The system prompt template is stored separately under `prompts/`

The difficulty lies in: this LLM is not the model that ultimately executes the task, but rather the scheduler that "controls the advancement of the OpenClaw task."

## 6.3 `src/openclaw_wrapper.py`

Role: wrap the OpenClaw CLI and agent/session-related operations.

Main capabilities:

- Read and save `~/.openclaw/openclaw.json`
- Configure global provider / skills
- Configure agent workspace / tools / skills / model
- List and delete worker agents
- reset / archive / restore session
- Parse OpenClaw's mixed stdout/stderr JSON output

This is the most core "system boundary layer" of the entire project.

## 6.4 `src/session_parser.py`

Role: parse the OpenClaw raw session JSONL into structured messages.

Core capabilities:

- Filter out records with `type=message`
- Extract text content and clean the OpenClaw CLI timestamp prefix
- Extract tool calls
- Extract tool results
- Extract the last complete agent response

A recent detail worth noting is:

- If a tool argument is a string but cannot be successfully `json.loads`'d, the project now **preserves the string as-is**, rather than faking a wrapping layer of `{"raw": ...}`
- This guarantees "lossless preservation" of the tool call arguments

## 6.5 `src/converter.py`

Role: convert the session into the training middle format.

Technical points include:

- OpenAI-style message construction
- tool_calls preserving structured arguments
- reasoning content extraction
- Dynamic system prompt construction
- skills section rendering
- tools schema extraction and fallback

This part essentially undertakes the task of "data productization."

## 6.6 `src/generation_support.py`

Role: provide shared logic for `run_generation.py`.

Contents include:

- The progress recorder `ProgressTracker`
- thinking mode parsing
- append query logic
- tools cache reading
- session metadata extraction
- middle format path and session path generation
- Final result aggregation

It extracts a large amount of miscellaneous logic from the main script, improving maintainability.

## 6.7 `src/worker_snapshot.py`

Role: worker-level resume recovery.

The approach is to persist the current unfinalized state of a worker:

- Current workspace snapshot
- Current session snapshot
- pending results
- The number of intents already processed in the current session

This way, even if interrupted, it can recover to "the most recent consistent state."

## 6.8 `src/runtime_recovery.py`

Role: handle issues such as OpenClaw runtime configuration contamination and gateway crashes.

Functions include:

- Detect whether the configuration file is corrupted
- Compute the configuration drift ratio
- Roll back the configuration from the baseline
- Call `openclaw doctor --fix`
- Restart the gateway

This is a very engineering-oriented capability: it not only records failures, but also attempts **automatic self-healing**.

## 6.9 `src/runtime_tools_proxy.py`

Role: capture the real runtime `tools` during the initialization phase.

Features:

- Implements an OpenAI-compatible proxy
- Can immediately return a minimal response after capturing in `capture_only` mode
- Does not depend on the upstream complete request finishing
- Saves `tool_names`, `tool_count`, `message_count` and the complete `tools`

This is a very typical design in the project of "sacrificing some complexity for accuracy, but ultimately keeping the complexity under control."

## 6.10 `src/fs_utils.py`

Role: handle read-only file issues during reset / cleanup.

Capabilities include:

- Recursively repair owner writable permissions
- Safely delete read-only files
- Safely delete read-only directory trees
- Restore the writable bit after copying a snapshot

This is a very typical engineering problem fix that "only surfaces during actual operation."

---

## 7. Key Technical Challenges of the Project

The truly valuable aspect of this project lies precisely in the fact that it solves many engineering problems that "seem small but are actually very troublesome."

### 7.1 Unified Abstraction of intent and direct_query

Input data is not necessarily naturally consistent:

- Some are user intents
- Some are direct search questions
- Some carry an answer, some do not
- Some have no stable ID

The project uses `normalize_task_record()` to unify them into the same task abstraction, which ensures that downstream flows do not need a branching explosion based on the input source.

### 7.2 Dual-Model Separation of Responsibilities

Many systems use only one model to do everything, but this project does not.

There are actually two layers of intelligence here:

1. `LLMClient`: determines the next query
2. OpenClaw agent: actually completes tool calls and execution

The difficulty of this design lies in:

- How the history is organized
- How completion determination is defined
- How query generation and agent execution are connected
- How the reasoning mode is made compatible across different models

### 7.3 The Balance Between Multi-worker and Single-worker Serial session

If sessions are run completely concurrently in a chaotic way, recovery and archiving become very complex;
if completely serial, it is too slow.

This project adopts:

- Concurrency between workers
- Serial within a worker
- Each worker bound to one agent

This is a very good engineering compromise.

### 7.4 session Deferred Finalization

If multiple intents are run consecutively within one session, then the "output" of the intermediate intents should not be materialized immediately and finally.

So the project introduces:

- `pending_session_results`
- worker runtime snapshot
- Unified archiving and conversion at finalize time

This mechanism significantly improves trajectory fidelity, but the implementation complexity also rises noticeably.

### 7.5 OpenClaw runtime Configuration Contamination Recovery

This is a very distinctive part of the project.

During real operation, the OpenClaw configuration may become corrupted due to exceptions, manual modification, or environment issues. The project does not simply "error out and exit," but instead:

- Identifies signals of configuration corruption
- Combines the baseline and drift ratio to judge whether to trigger recovery
- Rolls back the configuration
- Restarts the gateway
- Cleans up agents
- Automatically reruns generation

This shows that the project is no longer just a script, but possesses a certain "runtime autonomy capability."

### 7.6 Inconsistent True Source of Tool Definitions

Tool definitions are a key challenge in this project.

At the very beginning, one might take it for granted that:

- Statically scanned tool definitions = the tool definitions actually sent to the model

But this is not always the case in reality. Because the runtime also involves:

- Plugin registration
- allowlist trimming
- provider behavior
- The final outgoing payload

So the project ultimately adopts runtime probe capture to approximate the "real tool definitions," which is a very pragmatic solution.

### 7.7 Permission and Read-Only File Issues

During reset / restart / snapshot recovery, the generated artifacts sometimes become read-only, causing deletion to fail.

Such problems are usually not business logic problems, but typical runtime environment problems:

- File copy preserving mode
- Containers or tools generating read-only files
- Subsequent reset unable to delete

The project provides a unified fallback through `fs_utils.py`, significantly enhancing robustness.

---

## 8. Technical Highlights

### 8.1 Real Trajectory First, Not Simulated Trajectory First

The project does not fake a dataset that "looks like an Agent trajectory," but tries to construct data based on the real OpenClaw runtime.

This makes the data more credible in terms of tool calls, message format, and session state.

### 8.2 Taking "Orchestration" Rather Than "Intrusive Modification" of OpenClaw

The project does not heavily modify the OpenClaw body itself, but completes its orchestration through:

- CLI
- Configuration files
- workspace
- runtime proxy
- session files

This approach is more maintainable and more suitable for keeping up as the upstream version evolves.

### 8.3 Strong Runtime Recovery Capability

Compared to many "fail and exit" scripts, this project has:

- Progress-file resume
- worker snapshot
- runtime recovery
- gateway restart
- Automatic retry

This brings it closer to a production-grade data generation system.

### 8.4 runtime metadata Design Balances Accuracy and Debuggability

The project simultaneously retains:

- The real runtime metadata captured by the runtime probe (`tools` + `system_prompt`)
- The statically exported offline tool scanning capability
- The manually maintained `all_tools.json`

This is actually a good "three-layer runtime cognition system":

1. Offline static understanding
2. Real runtime capture
3. Manually controllable standard catalog

### 8.5 Data Conversion Design Close to Training Use Scenarios

The middle format is not simply a copy of the session, but a training-oriented structured organization, including:

- system message injection
- reasoning extraction
- tools structuring
- skills injection
- final_output aggregation

This allows the output to directly enter the downstream training or data analysis flow.

---

## 9. Output Artifacts Description

After the project runs, it mainly generates the following outputs:

### 9.1 Raw session

Directory: `output/sessions`

Features:

- Preserves the OpenClaw raw JSONL trajectory
- Can be used for problem troubleshooting, behavior auditing, and replay analysis

### 9.2 middle format

Directory: `output/middle_format`

Features:

- More unified structure
- More suitable for training and post-processing
- Each intent corresponds to one final JSON

### 9.3 runtime metadata and probe Snapshots

Directory: `output/worker_snapshots/runtime_metadata`

Includes:

- `runtime_metadata.json`
- `probe/runtime_probe_*_latest.json`
- `probe/runtime_probe_*.jsonl` history

### 9.4 Progress and Summary

Includes:

- `output/progress.json`
- `output/summary.json`

Used to record:

- The final status of each intent
- success / failed counts
- The number of global automatic recoveries
- Attempt history

### 9.5 runtime recovery Baseline

Directory: `output/runtime_recovery`

Includes:

- `openclaw.json.baseline`

Used for runtime recovery and configuration rollback.

---

## 10. Current Applicable Scenarios

This project is especially suitable for the following scenarios:

1. **Agent training dataset construction**
2. **Tool call behavior analysis**
3. **OpenClaw run trajectory archiving**
4. **Multi-task session sampling**
5. **Data generation for search / retrieval / automation agent tasks**
6. **Intermediate-format standardized output**

---

## 11. Current Limitations and Future Evolution Directions

### 11.1 Current Limitations

1. The runtime probe currently saves `message_count`, and does not fully persist `messages/system prompt`
2. `run_generation.py` is still rather long, and the main flow can be further split into modules
3. The tools cache is currently organized mainly by agent dimension, and may need a more explicit versioning strategy in the long term
4. Under complex runtimes, OpenClaw's external behavior may still change as the version evolves
5. Although automatic recovery is strong, it is still a "heuristic self-healing" and is not absolutely reliable

### 11.2 Directions for Continued Evolution

1. Save the complete messages in the probe request or save the system prompt separately
2. Further split worker_loop into a finer-grained state machine
3. Introduce more complete run metrics and tracing
4. Apply version stamps, hashes, or schema diff management to the tools cache
5. Add more regression tests to cover the session finalize / runtime recovery / resume pipeline
6. Optimize documentation and operations dashboards to lower the onboarding cost

---

## 12. Summary

The essence of `openclaw_gen_data` is not a simple "collection of scripts that call OpenClaw," but a complete engineering system built around Agent trajectory data construction.

Its value is reflected at three levels:

### 12.1 Engineering Value

It turns the originally fragile, manual, and hard-to-recover Agent data generation process into a repeatable, concurrent, and recoverable pipeline.

### 12.2 Data Value

What it generates is not pure simulated text, but real OpenClaw trajectories that are as faithful as possible, and it can convert them into a clearly structured training middle format.

### 12.3 Architecture Value

It nicely balances:

- Real runtime fidelity
- Tool definition accuracy
- OpenClaw compatibility
- Engineering robustness
- Data conversion usability

If this project is viewed from the perspective of a larger Agent data infrastructure, it already possesses the prototype of a "small production-grade data generation platform":

- It has input standardization
- It has execution scheduling
- It has runtime recovery
- It has intermediate artifacts
- It has final output
- It has tool system reconciliation
- It has containerization and CI support

This is also what makes this project most worth paying attention to: what it solves is not a single-point feature, but an entire Agent trajectory production pipeline.
