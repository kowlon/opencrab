# Best Practice (BP) Manual Mode - Runtime Flow

## Component Overview

```
+------------------+     +------------------+     +------------------+
|    Frontend      |     |   REST API       |     |   BPEngine       |
|    (SeeCrab UI)  |     |   /api/bp/*      |     |   (engine.py)    |
+--------+---------+     +--------+---------+     +--------+---------+
         |                        |                        |
         |    SSE Stream          |    async generator      |
         |<-----------------------|<-----------------------|
         |                        |                        |
+--------+---------+     +--------+---------+     +--------+---------+
|  BPStateManager  |     |  LinearScheduler |     |   Orchestrator   |
|  (state_manager) |     |  (scheduler.py)  |     | (orchestrator.py)|
+--------+---------+     +--------+---------+     +--------+---------+
         |                        |                        |
         |                        |               +--------+---------+
         |                        |               |    SubAgent      |
         |                        |               | (Agent instance) |
         |                        |               +------------------+
```

---

## Phase 0: System Initialization

```
+-------------+          +----------+        +-----------+        +---------+
|  server.py  |          | facade.py|        | BPEngine  |        |Orchestr.|
+------+------+          +----+-----+        +-----+-----+        +----+----+
       |                      |                    |                    |
       | init_bp_system()     |                    |                    |
       |--------------------->|                    |                    |
       |                      |                    |                    |
       |                      | new BPStateManager |                    |
       |                      |---------+          |                    |
       |                      |         |          |                    |
       |                      |<--------+          |                    |
       |                      |                    |                    |
       |                      | new BPEngine(sm)   |                    |
       |                      |------------------->|                    |
       |                      |                    |                    |
       |                      | BPConfigLoader     |                    |
       |                      |   .load_all()      |                    |
       |                      |  (scan YAML files) |                    |
       |                      |---------+          |                    |
       |                      |         |          |                    |
       |                      |<--------+          |                    |
       |                      |                    |                    |
       |                      | new BPToolHandler  |                    |
       |                      |---------+          |                    |
       |                      |<--------+          |                    |
       |                      |                    |                    |
       | set_bp_orchestrator() |                   |                    |
       |--------------------->|                    |                    |
       |                      | engine.set_orchestr|ator()              |
       |                      |------------------->|                    |
       |                      |                    | store ref          |
       |                      |                    |---------+          |
       |                      |                    |<--------+          |
       |                      |                    |                    |
```

---

## Phase 1: User Triggers BP (MasterAgent Path)

Two entry paths exist. Path A is via MasterAgent (LLM calls bp_start tool).
Path B is via Frontend direct API call. Both converge at the same BPEngine.

### Path A: MasterAgent → bp_start tool

```
+---------+       +-------------+       +-------------+       +----------+
|  User   |       | MasterAgent |       |BPToolHandler|       |BPStateMgr|
|         |       | (main Agent)|       | (handler.py)|       |          |
+----+----+       +------+------+       +------+------+       +-----+----+
     |                   |                     |                     |
     | "帮我做竞品分析"  |                     |                     |
     |------------------>|                     |                     |
     |                   |                     |                     |
     |                   | [LLM reasoning]     |                     |
     |                   | Matches BP trigger  |                     |
     |                   | keyword/command      |                     |
     |                   |                     |                     |
     |                   | tool_call: bp_start |                     |
     |                   | {bp_id, input_data, |                     |
     |                   |  run_mode:"manual"} |                     |
     |                   |-------------------->|                     |
     |                   |                     |                     |
     |                   |                     | check existing      |
     |                   |                     | active instance     |
     |                   |                     |-------------------->|
     |                   |                     |    None             |
     |                   |                     |<--------------------|
     |                   |                     |                     |
     |                   |                     | create_instance()   |
     |                   |                     |-------------------->|
     |                   |                     |                     |
     |                   |                     |   +-- Creates BPInstanceSnapshot:
     |                   |                     |   |   instance_id = "bp-a1b2c3d4"
     |                   |                     |   |   status = ACTIVE
     |                   |                     |   |   run_mode = MANUAL
     |                   |                     |   |   current_subtask_index = 0
     |                   |                     |   |   all subtask_statuses = PENDING
     |                   |                     |   |   initial_input = {user data}
     |                   |                     |   +-->
     |                   |                     |                     |
     |                   |                     |  instance_id        |
     |                   |                     |<--------------------|
     |                   |                     |                     |
     |                   |                     | push SSE event:     |
     |                   |                     | bp_instance_created |
     |                   |                     | → session.context   |
     |                   |                     |   ._sse_event_bus   |
     |                   |                     |-------+             |
     |                   |                     |       |             |
     |                   |                     |<------+             |
     |                   |                     |                     |
     |                   | "✅ 已创建 BP 实例" |                     |
     |                   |<--------------------|                     |
     |                   |                     |                     |
     | "已启动竞品分析..." |                    |                     |
     |<------------------|                     |                     |
```

### Path B: Frontend Direct API Call

```
+---------+       +---------------+       +----------+       +----------+
|Frontend |       |POST /api/bp/  |       |BPStateMgr|       | BPEngine |
|  (UI)   |       |    start      |       |          |       |          |
+----+----+       +-------+-------+       +-----+----+       +-----+----+
     |                    |                      |                  |
     | POST /api/bp/start |                     |                  |
     | {bp_id, session_id,|                     |                  |
     |  input_data,       |                     |                  |
     |  run_mode:"manual"}|                     |                  |
     |------------------->|                     |                  |
     |                    |                     |                  |
     |                    | _bp_mark_busy()     |                  |
     |                    |--------+            |                  |
     |                    |<-------+            |                  |
     |                    |  (acquire lock)     |                  |
     |                    |                     |                  |
     |                    | sm.create_instance()|                  |
     |                    |-------------------->|                  |
     |                    |   instance_id       |                  |
     |                    |<--------------------|                  |
     |                    |                     |                  |
     |                    | resolve_session()   |                  |
     |                    |--------+            |                  |
     |                    |<-------+            |                  |
     |                    |                     |                  |
     |  SSE: bp_instance_ |                     |                  |
     |       created      |                     |                  |
     |<-------------------|                     |                  |
     |                    |                     |                  |
     |                    | engine.advance(instance_id, session)   |
     |                    |--------------------------------------->|
     |                    |                     |                  |
     |                    |        (enters Phase 2 below)          |
```

---

## Phase 2: First Subtask Execution (Manual Mode)

```
+---------+  +----------+  +----------+  +----------+  +-----------+  +----------+
|Frontend |  | REST API |  | BPEngine |  |Scheduler |  |Orchestrat.|  | SubAgent |
|         |  |          |  |          |  |          |  |           |  |          |
+----+----+  +----+-----+  +----+-----+  +----+-----+  +-----+-----+  +----+-----+
     |            |              |             |              |              |
     |            |              |             |              |              |
     |            |   advance()  |             |              |              |
     |            |   (async     |             |              |              |
     |            |   generator) |             |              |              |
     |            |              |             |              |              |
     |            |              | yield bp_progress (initial)|              |
     |  SSE: bp_progress        |             |              |              |
     |<--------------------------|             |              |              |
     |            |              |             |              |              |
     |            |              | get_ready_tasks()          |              |
     |            |              |------------>|              |              |
     |            |              |             |              |              |
     |            |              |  [subtask_0]| (idx=0,      |              |
     |            |              |  (PENDING)  |  status=     |              |
     |            |              |<------------|  PENDING)    |              |
     |            |              |             |              |              |
     |            |              | resolve_input(subtask_0.id)|              |
     |            |              |------------>|              |              |
     |            |              |             |              |              |
     |            |              |  input_data | (from        |              |
     |            |              |<------------| initial_input|              |
     |            |              |             | since idx=0) |              |
     |            |              |             |              |              |
     |            |              | _check_input_completeness()|              |
     |            |              |-------+     |              |              |
     |            |              |       |     |              |              |
     |            |              |<------+     |              |              |
     |            |              | (OK, no     |              |              |
     |            |              |  missing)   |              |              |
     |            |              |             |              |              |
     |            |              | update_subtask_status      |              |
     |            |              |  (CURRENT)  |              |              |
     |            |              |             |              |              |
     |            |              | yield bp_subtask_start     |              |
     |  SSE: bp_subtask_start   |             |              |              |
     |<--------------------------|             |              |              |
     |            |              |             |              |              |
     |            |              |             |              |              |
     |            |              | _run_subtask_stream()      |              |
     |            |              |             |              |              |
     |            |              | _build_delegation_message()|              |
     |            |              |-------+     |              |              |
     |            |              |<------+     |              |              |
     |            |              |  (includes: BP name,       |              |
     |            |              |   subtask name, input JSON,|              |
     |            |              |   output schema template,  |              |
     |            |              |   constraints)             |              |
     |            |              |             |              |              |
     |            |              | Swap SSE event_bus to temp Queue          |
     |            |              |-------+     |              |              |
     |            |              |<------+     |              |              |
     |            |              |             |              |              |
     |            |              | orchestrator.delegate(     |              |
     |            |              |   session, "bp_engine",    |              |
     |            |              |   subtask.agent_profile,   |              |
     |            |              |   message,                 |              |
     |            |              |   session_messages=[])     |  <-- Context isolation!
     |            |              |-------------|------------->|              |
     |            |              |             |              |              |
     |            |              |             |              | _dispatch()  |
     |            |              |             |              |-------+      |
     |            |              |             |              |       |      |
     |            |              |             |              | ProfileStore |
     |            |              |             |              | .get(profile)|
     |            |              |             |              |-------+      |
     |            |              |             |              |<------+      |
     |            |              |             |              |              |
     |            |              |             |              | AgentPool    |
     |            |              |             |              | .get_or_create()
     |            |              |             |              |-------+      |
     |            |              |             |              |<------+      |
     |            |              |             |              |              |
     |            |              |             |              | create_task( |
     |            |              |             |              |  _call_agent)|
     |            |              |             |              |------------->|
     |            |              |             |              |              |
     |            |              |             |              |              | chat_with_
     |            |              |             |              |              | session_stream()
     |            |              |             |              |              |-----+
     |            |              |             |              |              |     |
     |            |              |             |              |              |     | ReAct Loop
     |            |              |             |              |              |     | Think→Act→Observe
     |            |              |             |              |              |     |
```

### SubAgent Streaming Events Flow

```
+---------+  +----------+  +----------+              +-----------+  +----------+
|Frontend |  | REST API |  | BPEngine |              |Event Queue|  | SubAgent |
|         |  |          |  | (event   |              | (temp bus)|  |          |
|         |  |          |  |  loop)   |              |           |  |          |
+----+----+  +----+-----+  +----+-----+              +-----+-----+  +----+-----+
     |            |              |                          |              |
     |            |              |                          |              |
     |            |              |                          | thinking_delta
     |            |              |                          |<-------------|
     |            |              | event_bus.get()          |              |
     |            |              |------------------------->|              |
     |            |              |  {type:thinking_delta}   |              |
     |            |              |<-------------------------|              |
     |            |              |                          |              |
     |            |              | yield {type:"thinking"}  |              |
     |  SSE: thinking            |                         |              |
     |<--------------------------|                          |              |
     |            |              |                          |              |
     |            |              |                          | tool_call_start
     |            |              |                          |<-------------|
     |            |              | event_bus.get()          |              |
     |            |              |------------------------->|              |
     |            |              |  {type:tool_call_start}  |              |
     |            |              |<-------------------------|              |
     |            |              |                          |              |
     |            |              | StepFilter.classify()    |              |
     |            |              | StepAggregator           |              |
     |            |              |  .on_tool_call_start()   |              |
     |            |              |-------+                  |              |
     |            |              |<------+                  |              |
     |            |              |                          |              |
     |            |              | yield step_card (running)|              |
     |  SSE: step_card           |                         |              |
     |<--------------------------|                          |              |
     |            |              |                          |              |
     |            |              |                          | tool_call_end
     |            |              |                          |<-------------|
     |            |              | event_bus.get()          |              |
     |            |              |------------------------->|              |
     |            |              |  {type:tool_call_end}    |              |
     |            |              |<-------------------------|              |
     |            |              |                          |              |
     |            |              | Aggregator               |              |
     |            |              |  .on_tool_call_end()     |              |
     |            |              | Capture tool result      |              |
     |            |              |-------+                  |              |
     |            |              |<------+                  |              |
     |            |              |                          |              |
     |            |              | yield step_card (updated)|              |
     |  SSE: step_card           |                         |              |
     |<--------------------------|                          |              |
     |            |              |                          |              |
     |            |              |  ... (more tool calls    |              |
     |            |              |   and thinking blocks)   |              |
     |            |              |                          |              |
     |            |              |                          | delegate_task
     |            |              |                          | .done() = true
     |            |              |                          |<-------------|
     |            |              |                          |              |
     |            |              | raw_result = await       |              |
     |            |              |   delegate_task          |              |
     |            |              |-------+                  |              |
     |            |              |<------+                  |              |
     |            |              |                          |              |
     |            |              | Restore original event_bus              |
     |            |              |-------+                  |              |
     |            |              |<------+                  |              |
     |            |              |                          |              |
     |            |              | yield {type:             |              |
     |            |              |  "_internal_output",     |              |
     |            |              |  data: parsed_json,      |              |
     |            |              |  raw_result: text,       |              |
     |            |              |  tool_results: [...]}    |              |
     |            |              |                          |              |
```

---

## Phase 3: Subtask Completion & Manual Mode Pause

```
+---------+  +----------+  +----------+  +----------+  +----------+
|Frontend |  | REST API |  | BPEngine |  |Scheduler |  |BPStateMgr|
|         |  |          |  | advance()|  |          |  |          |
+----+----+  +----+-----+  +----+-----+  +----+-----+  +----+-----+
     |            |              |             |              |
     |            |              |             |              |
     |            |              | (back in advance() after   |
     |            |              |  _run_subtask_stream)      |
     |            |              |             |              |
     |            |              | yield step_card            |
     |            |              |  (delegate completed)      |
     |  SSE: step_card           |             |              |
     |<--------------------------|             |              |
     |            |              |             |              |
     |            |              | _conform_output()          |
     |            |              |  (LLM call to map output   |
     |            |              |   to next subtask's        |
     |            |              |   input_schema)            |
     |            |              |-------+     |              |
     |            |              |<------+     |              |
     |            |              |             |              |
     |            |              | scheduler.complete_task()  |
     |            |              |------------>|              |
     |            |              |             | subtask_outputs[id] = output
     |            |              |             | subtask_statuses[id] = DONE
     |            |              |             | current_subtask_index += 1
     |            |              |             |              |
     |            |              | _persist_state()           |
     |            |              |-------------------------->.|
     |            |              |             | session.metadata["bp_state"]
     |            |              |             |              | = serialize()
     |            |              |             |              |
     |            |              | yield bp_subtask_complete  |
     |  SSE: bp_subtask_complete |             |              |
     |  {instance_id, subtask_id,|             |              |
     |   output, output_schema,  |             |              |
     |   summary}                |             |              |
     |<--------------------------|             |              |
     |            |              |             |              |
     |            |              | yield bp_progress          |
     |  SSE: bp_progress         |             |              |
     |  {statuses: {t1:"done",   |             |              |
     |   t2:"pending",...}}      |             |              |
     |<--------------------------|             |              |
     |            |              |             |              |
     |            |              |=== MANUAL MODE CHECK ===   |
     |            |              |             |              |
     |            |              | snap.run_mode == MANUAL    |
     |            |              |   → YES                    |
     |            |              |             |              |
     |            |              | yield bp_waiting_next      |
     |  SSE: bp_waiting_next     |             |              |
     |  {instance_id,            |             |              |
     |   next_subtask_index}     |             |              |
     |<--------------------------|             |              |
     |            |              |             |              |
     |            |              | return      |              |
     |            |              | (generator  |              |
     |            |              |  exhausted) |              |
     |            |              |             |              |
     |            | _persist_bp_to_session()   |              |
     |            |-------+      |             |              |
     |            |<------+      |             |              |
     |            |              |             |              |
     |  SSE: done |              |             |              |
     |<-----------|              |             |              |
     |            |              |             |              |
     |            | _bp_clear_busy()           |              |
     |            |-------+      |             |              |
     |            |<------+      |             |              |
     |            |              |             |              |
     | ========== USER REVIEWS OUTPUT, DECIDES NEXT ========= |
     |            |              |             |              |
```

---

## Phase 4: User Confirms Next Step

```
+---------+  +----------+  +----------+  +----------+  +-----------+  +----------+
|Frontend |  |POST /api/|  | BPEngine |  |Scheduler |  |Orchestrat.|  | SubAgent |
|  User   |  |bp/next   |  |          |  |          |  |           |  |  (new)   |
+----+----+  +----+-----+  +----+-----+  +----+-----+  +-----+-----+  +----+-----+
     |            |              |             |              |              |
     | Click "进入下一步"         |             |              |              |
     | or "开始执行"             |             |              |              |
     |            |              |             |              |              |
     | POST /api/bp/next        |             |              |              |
     | {instance_id, session_id} |             |              |              |
     |----------->|              |             |              |              |
     |            |              |             |              |              |
     |            | _ensure_bp_restored()      |              |              |
     |            |-------+      |             |              |              |
     |            |<------+      |             |              |              |
     |            |              |             |              |              |
     |            | _bp_mark_busy()            |              |              |
     |            |-------+      |             |              |              |
     |            |<------+      |             |              |              |
     |            |              |             |              |              |
     |            | _persist_user_message()    |              |              |
     |            |-------+      |             |              |              |
     |            |<------+      |             |              |              |
     |            |              |             |              |              |
     |            | engine.advance(instance_id)|              |              |
     |            |------------->|             |              |              |
     |            |              |             |              |              |
     |            |              | yield bp_progress          |              |
     |  SSE: bp_progress        |             |              |              |
     |<--------------------------|             |              |              |
     |            |              |             |              |              |
     |            |              | get_ready_tasks()          |              |
     |            |              |------------>|              |              |
     |            |              | [subtask_1] | (idx=1,      |              |
     |            |              |<------------| PENDING)     |              |
     |            |              |             |              |              |
     |            |              | resolve_input(subtask_1.id)|              |
     |            |              |------------>|              |              |
     |            |              |  output of  | (uses prev   |              |
     |            |              |  subtask_0  |  subtask's   |              |
     |            |              |<------------|  output)     |              |
     |            |              |             |              |              |
     |            |              | update_subtask_status      |              |
     |            |              |  (CURRENT)  |              |              |
     |            |              |             |              |              |
     |            |              | yield bp_subtask_start     |              |
     |  SSE: bp_subtask_start   |             |              |              |
     |<--------------------------|             |              |              |
     |            |              |             |              |              |
     |            |              | _run_subtask_stream()      |              |
     |            |              |-------------|------------->|              |
     |            |              |             |              |              |
     |            |              |             |   delegate() |              |
     |            |              |             |   session_messages=[]       |
     |            |              |             |              |------------->|
     |            |              |             |              |              |
     |            |              |   (streaming events flow   |   ReAct      |
     |            |              |    same as Phase 2)        |   Loop       |
     |  SSE: thinking, step_cards|             |              |              |
     |<--------------------------|             |              |              |
     |            |              |             |              |              |
     |            |              | ... completion flow same as Phase 3 ...   |
     |            |              |             |              |              |
```

---

## Phase 5: Final Subtask Completion

```
+---------+  +----------+  +----------+  +----------+  +----------+
|Frontend |  | REST API |  | BPEngine |  |Scheduler |  |BPStateMgr|
|         |  |          |  | advance()|  |          |  |          |
+----+----+  +----+-----+  +----+-----+  +----+-----+  +----+-----+
     |            |              |             |              |
     |            |              | (after last subtask        |
     |            |              |  completes)                |
     |            |              |             |              |
     |            |              | scheduler.is_done()        |
     |            |              |------------>|              |
     |            |              |   true      |              |
     |            |              |<------------|              |
     |            |              |             |              |
     |            |              | sm.complete(instance_id)   |
     |            |              |-------------------------->.|
     |            |              |             |    status =  |
     |            |              |             |    COMPLETED |
     |            |              |             |              |
     |            |              | _persist_state()           |
     |            |              |-------------------------->.|
     |            |              |             |              |
     |            |              | yield bp_complete          |
     |  SSE: bp_complete         |             |              |
     |  {instance_id, bp_id,     |             |              |
     |   bp_name, outputs: {     |             |              |
     |     t1: {...},            |             |              |
     |     t2: {...},            |             |              |
     |     t3: {...}             |             |              |
     |   }}                      |             |              |
     |<--------------------------|             |              |
     |            |              |             |              |
     |            |              | return (generator done)    |
     |            |              |             |              |
     |  SSE: done |              |             |              |
     |<-----------|              |             |              |
     |            |              |             |              |
```

---

## Special Flow A: Missing Input (bp_ask_user)

```
+---------+  +----------+  +----------+  +----------+
|Frontend |  | REST API |  | BPEngine |  |BPStateMgr|
|         |  |          |  | advance()|  |          |
+----+----+  +----+-----+  +----+-----+  +----+-----+
     |            |              |              |
     |            |              | _check_input_completeness()
     |            |              |   → missing: ["field_x"]
     |            |              |              |
     |            |              | update_subtask_status
     |            |              |  (WAITING_INPUT)
     |            |              |------------->|
     |            |              |              |
     |            |              | yield bp_ask_user
     |  SSE: bp_ask_user        |              |
     |  {instance_id,           |              |
     |   subtask_id,            |              |
     |   missing_fields,        |              |
     |   input_schema}          |              |
     |<--------------------------|              |
     |            |              |              |
     |            |              | return       |
     |            |              |              |
     | ===== USER FILLS FORM ===|              |
     |            |              |              |
     | POST /api/bp/answer      |              |
     | {instance_id, subtask_id,|              |
     |  data: {field_x: "val"}} |              |
     |----------->|              |              |
     |            |              |              |
     |            | engine.answer()             |
     |            |------------->|              |
     |            |              |              |
     |            |              | merge data into
     |            |              | supplemented_inputs[subtask_id]
     |            |              |              |
     |            |              | update_subtask_status
     |            |              |  (PENDING)   |
     |            |              |------------->|
     |            |              |              |
     |            |              | advance()    |
     |            |              | (re-execute, |
     |            |              |  now input   |
     |            |              |  is complete)|
     |            |              |              |
     |            |              | ... normal subtask execution flow ...
```

---

## Special Flow B: Edit Output (Chat-to-Edit)

```
+---------+  +----------+  +----------+  +----------+
|Frontend |  |PUT /api/ |  | BPEngine |  |BPStateMgr|
|         |  |bp/edit-  |  |          |  |          |
|         |  |output    |  |          |  |          |
+----+----+  +----+-----+  +----+-----+  +----+-----+
     |            |              |              |
     | User edits subtask output |              |
     |            |              |              |
     | PUT /api/bp/edit-output   |              |
     | {instance_id, subtask_id, |              |
     |  changes: {key: new_val}} |              |
     |----------->|              |              |
     |            |              |              |
     |            | engine.handle_edit_output()  |
     |            |------------->|              |
     |            |              |              |
     |            |              | sm.merge_subtask_output()
     |            |              |------------->|
     |            |              |  (deep merge)|
     |            |              |<-------------|
     |            |              |              |
     |            |              | sm.mark_downstream_stale()
     |            |              |------------->|
     |            |              |              | all DONE tasks after
     |            |              |              | this one → STALE
     |            |              |<-------------|
     |            |              |  [stale_ids] |
     |            |              |              |
     |            | {success, merged,           |
     |            |  stale_subtasks}            |
     |            |<-------------|              |
     |            |              |              |
     |  200 OK    |              |              |
     |<-----------|              |              |
     |            |              |              |
     | User clicks "next" →      |              |
     | POST /api/bp/next         |              |
     | → advance() re-executes   |              |
     |   STALE subtasks          |              |
```

---

## Special Flow C: Task Switching

```
+---------+  +-------------+  +----------+  +-------------+
|MasterAgt|  |BPToolHandler|  |BPStateMgr|  |ContextBridge|
|         |  |             |  |          |  |             |
+----+----+  +------+------+  +----+-----+  +------+------+
     |              |              |               |
     | bp_switch_task              |               |
     | {target_instance_id}        |               |
     |------------->|              |               |
     |              |              |               |
     |              | suspend(current_active)      |
     |              |------------->|               |
     |              |              | status=SUSPENDED
     |              |              |               |
     |              | resume(target_id)            |
     |              |------------->|               |
     |              |              | status=ACTIVE |
     |              |              |               |
     |              | set_pending_switch()         |
     |              |------------->|               |
     |              |              | enqueue       |
     |              |              | PendingContextSwitch
     |              |              |               |
     |              |              |               |
     | (next reasoning turn)       |               |
     |              |              |               |
     | Agent._pre_reasoning_hook() |               |
     |----------------------------+|               |
     |              |              |               |
     | consume_pending_switch()    |               |
     |----------------------------->               |
     |              |              |               |
     |              |              | execute_pending_switch()
     |              |              |<--------------|
     |              |              |               |
     |              |              | _compress_context()
     |              |              |  (summarize suspended)
     |              |              |               |
     |              |              | _restore_context()
     |              |              |  (inject recovery msg)
     |              |              |               |
```

---

## Complete SSE Event Timeline (Manual Mode, 3 Subtasks)

```
TIME  EVENT                     DATA HIGHLIGHTS
----  -----                     ---------------
  │
  │   bp_instance_created       instance_id, bp_id, subtasks[]
  │
  │   ═══════ Subtask 1 ═══════
  │
  │   bp_progress               {t1:pending, t2:pending, t3:pending}
  │   bp_subtask_start          subtask_id=t1
  │   thinking                  SubAgent thinking content...
  │   step_card (delegate)      status=running
  │   step_card (tool)          web_search → running
  │   step_card (tool)          web_search → completed
  │   step_card (tool)          read_file → running → completed
  │   step_card (delegate)      status=completed, duration=45s
  │   bp_subtask_complete       output={...}, summary="..."
  │   bp_progress               {t1:done, t2:pending, t3:pending}
  │   bp_waiting_next           next_subtask_index=1
  │   done
  │
  │   ══ USER REVIEWS & CLICKS "NEXT" ══
  │
  │   ═══════ Subtask 2 ═══════
  │
  │   bp_progress               {t1:done, t2:pending, t3:pending}
  │   bp_subtask_start          subtask_id=t2
  │   thinking                  ...
  │   step_card (delegate)      status=running
  │   ...tool events...
  │   step_card (delegate)      status=completed
  │   bp_subtask_complete       output={...}
  │   bp_progress               {t1:done, t2:done, t3:pending}
  │   bp_waiting_next           next_subtask_index=2
  │   done
  │
  │   ══ USER REVIEWS & CLICKS "NEXT" ══
  │
  │   ═══════ Subtask 3 (Final) ═══════
  │
  │   bp_progress               {t1:done, t2:done, t3:pending}
  │   bp_subtask_start          subtask_id=t3
  │   thinking                  ...
  │   ...tool events...
  │   step_card (delegate)      status=completed
  │   bp_subtask_complete       output={...}
  │   bp_progress               {t1:done, t2:done, t3:done}
  │   bp_complete               outputs={t1:{...}, t2:{...}, t3:{...}}
  │   done
  │
  ▼
```

---

## Data Flow Between Subtasks

```
                   ┌─────────────────────────────────────────────┐
                   │              initial_input                   │
                   │  {topic: "竞品分析", competitors: [...]}     │
                   └────────────────────┬────────────────────────┘
                                        │
                                        ▼
                   ┌─────────────────────────────────────────────┐
                   │           Subtask 1 (数据收集)               │
                   │  agent_profile: "data_collector"             │
                   │  input: initial_input                       │
                   │  output: {raw_data: [...], sources: [...]}  │
                   └────────────────────┬────────────────────────┘
                                        │
                            _conform_output()
                          (LLM maps output to
                           next input_schema)
                                        │
                                        ▼
                   ┌─────────────────────────────────────────────┐
                   │           Subtask 2 (数据分析)               │
                   │  agent_profile: "data_analyst"               │
                   │  input: subtask_1.output (conformed)        │
                   │  output: {insights: [...], trends: [...]}   │
                   └────────────────────┬────────────────────────┘
                                        │
                            _conform_output()
                                        │
                                        ▼
                   ┌─────────────────────────────────────────────┐
                   │           Subtask 3 (报告生成)               │
                   │  agent_profile: "report_writer"              │
                   │  input: subtask_2.output (conformed)        │
                   │  output: {report: "...", files: [...]}      │
                   └─────────────────────────────────────────────┘


  Input Resolution Logic (LinearScheduler.resolve_input):

  ┌──────────────────────────────────────────────────────────┐
  │  if subtask.input_mapping:                               │
  │      base = {field: outputs[upstream_id] for each map}   │
  │  elif idx == 0:                                          │
  │      base = initial_input                                │
  │  else:                                                   │
  │      base = subtask_outputs[prev_subtask_id]             │
  │                                                          │
  │  base.update(supplemented_inputs[subtask_id])  // merge  │
  └──────────────────────────────────────────────────────────┘
```

---

## State Machine

```
  BPInstance Status:
  ┌─────────┐     create      ┌────────┐
  │         │ ──────────────> │ ACTIVE │──────────────┐
  │  (new)  │                 └───┬────┘              │
  └─────────┘                     │                   │
                          suspend │ resume            │ all subtasks
                                  │                   │ complete
                            ┌─────▼──────┐            │
                            │ SUSPENDED  │            │
                            └────────────┘       ┌────▼─────┐
                                                 │COMPLETED │
                                                 └──────────┘
                   cancel from ACTIVE/SUSPENDED:
                            ┌────────────┐
                            │ CANCELLED  │
                            └────────────┘


  Subtask Status:
  ┌─────────┐               ┌─────────┐              ┌──────┐
  │ PENDING │──────────────>│ CURRENT │─────────────>│ DONE │
  └────┬────┘   execution   └────┬────┘  success     └──┬───┘
       │        starts           │                      │
       │                         │ error           edit output
       │                    ┌────▼────┐            (downstream)
       │                    │ FAILED  │                 │
       │                    └─────────┘            ┌────▼───┐
       │                                           │ STALE  │
       │              input missing                └────┬───┘
       │         ┌──────────────────┐                   │
       └────────>│  WAITING_INPUT   │───────────────────┘
                 └──────────────────┘   user provides    re-execute
                         │              data → PENDING    → PENDING
                         └──────────────────────┘
```

---

## Concurrency & Safety

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                    Busy Lock (per session)                      │
  │                                                                 │
  │  /api/bp/start  ──┐                                            │
  │  /api/bp/next   ──┼── _bp_mark_busy(session_id) ──► 409 if    │
  │  /api/bp/answer ──┘    locked                       busy       │
  │                                                                 │
  │  TTL = 600s (auto-expire stale locks)                          │
  │  _bp_renew_busy() called on bp_subtask_complete                │
  │  _bp_clear_busy() in finally block                             │
  │                                                                 │
  ├─────────────────────────────────────────────────────────────────┤
  │                                                                 │
  │                 Disconnect Watcher                              │
  │                                                                 │
  │  asyncio.Task polls request.is_disconnected() every 2s         │
  │  On disconnect:                                                │
  │    → set disconnect_event                                      │
  │    → cancel session.context._bp_delegate_task                  │
  │    → SSE stream stops yielding                                 │
  │                                                                 │
  ├─────────────────────────────────────────────────────────────────┤
  │                                                                 │
  │              SubAgent Context Isolation                         │
  │                                                                 │
  │  orchestrator.delegate(session_messages=[])                    │
  │    → SubAgent gets NO conversation history                     │
  │    → Only sees delegation message with input_data              │
  │    → Prevents cross-contamination between subtasks             │
  │                                                                 │
  ├─────────────────────────────────────────────────────────────────┤
  │                                                                 │
  │              Progress-Aware Timeout                             │
  │                                                                 │
  │  Orchestrator polls agent's fingerprint every 3s:              │
  │    (iteration, status, tools_count)                            │
  │  If no change for idle_timeout (1200s) → kill agent            │
  │  If hard_timeout reached → kill agent                          │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘
```

---

## Persistence & Recovery

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                                                                 │
  │  After each subtask completion:                                │
  │                                                                 │
  │    session.metadata["bp_state"] = {                            │
  │      "version": 1,                                             │
  │      "instances": [                                            │
  │        {                                                       │
  │          "bp_id": "competitor_analysis",                       │
  │          "instance_id": "bp-a1b2c3d4",                        │
  │          "status": "active",                                   │
  │          "current_subtask_index": 1,                           │
  │          "run_mode": "manual",                                 │
  │          "subtask_statuses": {"t1":"done","t2":"pending",...}, │
  │          "subtask_outputs": {"t1": {...}},                     │
  │          "initial_input": {...},                                │
  │          "supplemented_inputs": {}                             │
  │        }                                                       │
  │      ],                                                        │
  │      "cooldown": 0                                             │
  │    }                                                           │
  │                                                                 │
  │  On server restart / page refresh:                             │
  │                                                                 │
  │    _ensure_bp_restored(request, session_id, sm)                │
  │      → sm.get_all_for_session() empty?                         │
  │      → load session.metadata["bp_state"]                       │
  │      → sm.restore_from_dict()                                  │
  │      → re-fill bp_config from BPConfigLoader                   │
  │      → resume from current_subtask_index                       │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘
```

---

## Key Source Files

```
src/seeagent/bestpractice/
├── models.py          Enums, Config dataclasses, BPInstanceSnapshot
├── state_manager.py   BPStateManager - instance lifecycle, persistence
├── engine.py          BPEngine - advance(), _run_subtask_stream(), _conform_output()
├── scheduler.py       LinearScheduler - task ordering, input resolution
├── handler.py         BPToolHandler - bp_start, bp_edit_output, bp_switch_task
├── facade.py          Singleton factory, init_bp_system(), prompt injection
├── context_bridge.py  Task switching context compression/restoration
├── config_loader.py   YAML config scanning, profile registration
├── config.py          YAML parsing & validation
├── tool_definitions.py  Tool definitions for LLM
└── prompt_loader.py   System prompt template rendering

src/seeagent/agents/
└── orchestrator.py    AgentOrchestrator - delegate(), _call_agent_streaming()

src/seeagent/api/routes/
└── bestpractice.py    REST endpoints: /start, /next, /answer, /status, /edit-output
```
