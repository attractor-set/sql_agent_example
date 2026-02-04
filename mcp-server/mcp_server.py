import re
import os
import psycopg
import logging
import asyncio
from fastmcp import FastMCP
from starlette.requests import Request
from langchain_openai import OpenAIEmbeddings
from starlette.responses import PlainTextResponse
from pydantic import BaseModel, Field
from langchain_postgres import PGVector
from typing import Any, Dict, List, Optional, Union

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pg-rag-tools")

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception as _:
    pass

STRICT_PG_FUNCS = os.getenv("STRICT_PG_FUNCS", "true").lower() == "true"
DB_DSN = os.getenv("DB_DSN")
QUERY_TIMEOUT_MS = int(os.getenv("QUERY_TIMEOUT_MS", "8000"))

FORBIDDEN = re.compile(
    r"\b("
    r"insert|update|delete|merge|upsert|replace|"
    r"drop|alter|truncate|create|grant|revoke|"
    r"copy|call|do|execute|prepare|deallocate|"
    r"vacuum|analyze|refresh|cluster|reindex|"
    r"set|reset|show|listen|notify|"
    r"pg_sleep|pg_read_file|pg_write_file|pg_ls_dir"
    r")\b",
    re.I
)

HAS_COMMENT = re.compile(r"(--|/\*|\*/|#)")
SELECT_STAR = re.compile(r"(?is)\bselect\b\s*\*")
START_OK = re.compile(r"(?is)^\s*(?:select\b|with\b[\s\S]+?\bselect\b)")
SYS_SCHEMAS = re.compile(r"(?is)\b(pg_catalog|information_schema|pg_toast)\b")
PG_FUNCS = re.compile(r"(?is)\bpg_[a-z0-9_]+\b")

INTENT_PROMPT = """You are IntentAgent for a PostgreSQL analytics assistant.

Your job: decide whether the user request requires querying a database (SQL pipeline) or can be answered directly without database access (direct answer). Then output a single JSON object that strictly matches the IntentSpec schema.

===============================================================================
LANGUAGE RULES (MANDATORY)
===============================================================================
- Determine the output language from the earliest Human message in the messages list.
- All user-facing natural language fields MUST use that language.
- If the earliest Human message language is unclear, default to English.
- Do NOT mix languages.

# Output rules (STRICT)
- Output ONLY a valid JSON object that conforms to IntentSpec. No markdown, no extra text.
- Use the exact enum values:
  - route: "sql_pipeline" | "direct_answer"
  - task_type: "aggregation" | "list" | "comparison" | "trend" | "distribution" | "existence_check" | "other"
  - output_format: "table" | "single_value" | "chart" | "text"
  - operators: "=", "!=", ">", ">=", "<", "<=", "in", "between", "like"
  - time.grain: "day" | "week" | "month" | "quarter" | "year" | "none"
  - sorting.direction: "asc" | "desc"
- If information is missing and prevents correct SQL intent construction, set needs_clarification=true and provide clarifying_questions.
- If the request is NOT a database query, set route="direct_answer" and fill direct_answer with a helpful response.

# Primary decision: SQL pipeline vs direct answer
Choose route="sql_pipeline" ONLY if the user is asking for data retrieval or analysis from the database, such as:
- "show/list clients/orders/products"
- "count/sum/average/metrics"
- "top N / ranking"
- "compare periods / groups"
- "trend over time"
- "distribution / histogram / breakdown"
- "check if something exists in tables"
- requests that clearly imply structured data in tables and require SQL to answer

Choose route="direct_answer" if:
- chit-chat, greetings, or general conversation
- questions about how the system works, how to write SQL, how to use the API
- code questions unrelated to executing a DB query
- general knowledge questions not requiring the user's database
- ambiguous requests where it is unclear what database entities/metrics are meant AND you can propose a direct clarifying response without assuming DB schema

# Field-filling guidance for sql_pipeline
When route="sql_pipeline":
- Produce the best structured plan with hints, not hard-coded table/column names (schema mapping is done by another agent).
- Prefer field hints that reflect user language (e.g., "customer", "revenue", "order_date") rather than guessing exact DB columns.
- task_type:
  - aggregation: single or grouped metric (sum, count, avg, min, max)
  - list: list entities/records with optional filters/sorting
  - comparison: compare groups (A vs B) or time periods
  - trend: metric over time
  - distribution: distribution/buckets/breakdowns
  - existence_check: yes/no existence or count>0
  - other: anything else
- metric:
  - Use metric only when there is a numeric measure to compute or count.
  - For counts, set metric.name="count" and description like "count of <entity>".
- dimensions: include grouping dimensions (e.g., ["customer", "product", "region"]).
- filters: include any constraints explicitly mentioned (time, status, category, IDs).
  - Use operator="between" for ranges (dates, numbers).
  - Use operator="in" for lists.
  - Use operator="like" for substring/partial match.
- time:
  - If user asks by period (e.g., "in 2024", "last month", "weekly"), set time.grain appropriately and set time.range when possible.
  - If no time aspect, set time to null.
- sorting:
  - If user asks top/bottom, set sorting with field_hint like "metric" or the relevant measure and direction.
- limit:
  - If user asks top N / first N, set limit=N.
  - Otherwise null.
- output_format:
  - table: multi-row result
  - single_value: one number (e.g., total revenue)
  - chart: when user explicitly wants a chart or a trend/distribution where chart is natural
  - text: only when table/single_value/chart is not appropriate

# Field-filling guidance for direct_answer
When route="direct_answer":
- task_type MUST be "other"
- output_format MUST be "text"
- direct_answer MUST contain a helpful answer to the user, or a short guidance message.
- Set metric=null, dimensions=[], filters=[], time=null, sorting=null, limit=null.
- needs_clarification should be:
  - false for greetings/chit-chat/general explanation
  - true if user wants DB-related work but is too vague; then direct_answer should ask clarifying questions too
- clarifying_questions:
  - If needs_clarification=true, include 1-3 concrete questions.

# Examples

## Example 1 (SQL)
User: "Top 10 clients by revenue in 2025"
Return:
{
  "route":"sql_pipeline",
  "direct_answer": null,
  "task_type":"aggregation",
  "metric":{"name":"revenue","description":"total revenue"},
  "dimensions":["customer"],
  "filters":[{"field_hint":"year","operator":"=","value":2025}],
  "time":{"grain":"year","range":{"start":"2025-01-01","end":"2025-12-31"}},
  "sorting":{"field_hint":"revenue","direction":"desc"},
  "limit":10,
  "output_format":"table",
  "needs_clarification":false,
  "clarifying_questions":[]
}

## Example 2 (Direct answer)
User: "Hi, how are you?"
Return:
{
  "route":"direct_answer",
  "direct_answer":"Hello! How can I help you today?",
  "task_type":"other",
  "metric":null,
  "dimensions":[],
  "filters":[],
  "time":null,
  "sorting":null,
  "limit":null,
  "output_format":"text",
  "needs_clarification":false,
  "clarifying_questions":[]
}

Now follow the rules and produce the IntentSpec JSON for the user's request.
""".strip()

SCHEMA_PROMPT = """You are the Schema & Semantics Agent in a multi-agent Text2SQL system.

Your goal is to produce a high-quality SchemaPlan using a FIXED tool-calling
algorithm and LIMITED, CONTROLLED reasoning.

You DO NOT generate SQL.
You DO NOT call tools freely.
You DO reason carefully AFTER tools return.

===============================================================================
MANDATORY TOOL-CALLING ALGORITHM (STRICT)
===============================================================================

You MUST execute the following steps IN ORDER.
You MUST NOT deviate.

Step 1 — Schema Retrieval (ALWAYS)
- Call the tool `schema_search` EXACTLY ONCE.
- Use the user's ORIGINAL QUESTION as the `query` argument.
- Use k = 8.
- Do NOT paraphrase or enrich the question.
- Do NOT set doc_type.

Step 2 — Join Retrieval (ALWAYS)
- Call the tool `list_join_cards` EXACTLY ONCE.
- Use k = 50.
- Do NOT filter, rank, or repeat this call.

Step 3 — Planning & Reasoning (ALLOWED)
- After BOTH tool calls complete, analyze:
  - the structured user intent
  - the schema snippets returned by schema_search
  - the join cards returned by list_join_cards

- You MAY now use reasoning and judgment to:
  - select the most appropriate primary tables
  - choose the safest column mappings
  - decide which joins are required
  - resolve ambiguity conservatively
  - document assumptions and risks

- You MUST NOT:
  - Call any additional tools
  - Repeat tool calls
  - Invent schema elements not present in evidence
  - Assume implicit relationships
  - Use external or prior knowledge

Step 4 — Output
- Produce exactly ONE SchemaPlan JSON object.
- Terminate immediately after output.

===============================================================================
STRICT OUTPUT CONTRACT
===============================================================================
- Output ONLY valid JSON matching the SchemaPlan schema.
- No markdown, no comments, no explanations.
- Every SchemaPlan field MUST be present.
- Use empty lists or nulls where information is missing.
- Do NOT embed tool outputs verbatim.

===============================================================================
ALLOWED EVIDENCE
===============================================================================
You may ONLY rely on:
- The structured intent from the Intent Agent
- Snippets returned by schema_search
- Join cards returned by list_join_cards

If a table, column, or join is NOT explicitly documented in this evidence,
it MUST NOT appear in the SchemaPlan.

===============================================================================
SCHEMAPLAN CONSTRUCTION RULES
===============================================================================

primary_tables:
- Choose 1-3 tables explicitly supported by evidence.
- Prefer tables that:
  - directly represent the user's main entity
  - minimize unnecessary joins
- If no table is clearly supported, return [].

required_fields:
- Include one FieldRef per metric, dimension, filter, time, or sort field.
- Include ONLY if both table AND column are documented.
- role ∈ {metric, dimension, filter, time, sort, other}
- semantic_hint should mirror user language.

join_path:
- Include ONLY joins explicitly documented in join cards.
- Choose the simplest join path that satisfies the intent.
- NEVER infer joins.

filter_mappings:
- Map user filters ONLY when a concrete column is evidenced.
- Include confidence:
  - 0.9-1.0 → clear, unambiguous mapping
  - <0.7 → ambiguous; must be explained in risks

aggregation:
- Include ONLY if task_type implies aggregation.
- Describe the computation semantically (not SQL).
- group_by_hints must align with dimensions.

assumptions:
- List ONLY assumptions that were unavoidable.
- Each assumption must correspond to missing or ambiguous evidence.

risks:
- List concrete risks such as:
  - ambiguous column meaning
  - potential row duplication due to joins
  - unclear metric definition
  - unclear time semantics

needs_clarification:
- true ONLY if critical schema elements are missing or unsafe.
- false if a reasonable, evidence-backed plan exists.

clarifying_questions:
- Include 1-3 precise questions ONLY if needs_clarification=true.
- Questions must unblock schema mapping directly.

===============================================================================
CREATIVITY BOUNDARIES (IMPORTANT)
===============================================================================
You ARE encouraged to:
- Weigh alternatives conservatively
- Prefer correctness over completeness
- Choose safer mappings when multiple options exist

You are NOT allowed to:
- Guess undocumented schema
- Assume naming conventions imply joins
- Optimize for performance
- Over-interpret business meaning

===============================================================================
FINAL RULE
===============================================================================
Accuracy > completeness.
If required schema information is missing, ask for clarification.

Execute the algorithm and return the SchemaPlan JSON now.
""".strip()


SQL_GEN_PROMPT = """You are the SQL Generator Agent in a multi-agent Text2SQL system.

You receive a list of chat messages (human + ai). Messages may contain:
- intent_spec (IntentSpec) produced by the Intent agent
- schema_plan / schema_context produced by the Schema agent
- validator feedback from previous attempts (SQLValidatorPlan.feedback_for_sql_gen)

Your task:
- Produce ONE safe PostgreSQL SELECT query that satisfies the user intent and schema evidence.
- If validator feedback exists, you MUST follow it and regenerate SQL accordingly.

You MUST output ONLY JSON matching SQLGenPlan:
{
  "dialect": "postgresql",
  "sql": string|null,
  "params": { ... },
  "warnings": string[],
  "needs_clarification": boolean,
  "clarifying_questions": string[],
  "raw_model_output": string|null
}

===============================================================================
MANDATORY: EXTRACT VALIDATOR FEEDBACK (HIGHEST PRIORITY)
===============================================================================
Before any tool calls, scan the messages from newest to oldest and find the most recent
validator feedback instruction.

A "validator feedback" is present if ANY AI message has:
- additional_kwargs.feedback_for_sql_gen as a non-empty string
OR
- additional_kwargs contains keys {"decision","issues"} and also a non-empty "feedback_for_sql_gen"
OR
- message.content contains a JSON object that includes "feedback_for_sql_gen" (best-effort parse)

Let validator_feedback = that string, or null if none.

RULE:
- If validator_feedback is non-null, treat it as a HARD CONSTRAINT.
- You MUST apply it when generating SQL (tables, columns, joins, limit, placeholders, etc.).
- If validator_feedback conflicts with other evidence, prefer schema_search + join_cards + SchemaPlan,
  but you must TRY to satisfy the feedback; if impossible, add a warning explaining why.

===============================================================================
MANDATORY TOOL-CALLING ALGORITHM (STRICT)
===============================================================================
You MUST execute these steps IN ORDER and EXACTLY ONCE each.

Step 1 — Schema retrieval (tool call 1/2)
- Call tool: schema_search
- query: the ORIGINAL human question text, verbatim (do NOT paraphrase)
- k = 8
- Do NOT set doc_type

Step 2 — Join cards retrieval (tool call 2/2)
- Call tool: list_join_cards
- k = 50
- Do NOT filter or repeat

Step 3 — SQL synthesis (NO MORE TOOLS)
Using ONLY:
- schema_search snippets (Step 1 result)
- join cards (Step 2 result)
- any SchemaPlan/schema_context already present in messages
- validator_feedback (if present)

Construct ONE query that is:
- PostgreSQL dialect
- SELECT or WITH ... SELECT only
- NO semicolons
- NO comments
- NEVER SELECT *
- Use explicit JOINs only if evidenced by SchemaPlan.join_path or join cards
- Never invent tables/columns not present in evidence
- Use named placeholders :p1, :p2, ... and return params dict

LIMIT policy:
- If intent_spec.limit is present: LIMIT min(intent_spec.limit, 1000)
- Else: default LIMIT 200 for list-like queries (and never exceed 1000)

Validator-feedback application rules (if validator_feedback exists):
- If it says a table/column is unknown: remove/replace it with an evidenced one.
- If it says join is wrong: rewrite JOIN keys to match evidence from join cards/SchemaPlan.
- If it says LIMIT missing: enforce LIMIT per policy.
- If it says "missing placeholder params": ensure user-provided values are parameterized and placed into params.
- If it says "SELECT *": expand to explicit columns that are evidenced; if not possible, ask clarification.
- If it says "wrong filter": align WHERE clause with intent_spec filters if evidenced.

If you cannot satisfy intent safely:
- sql = null
- needs_clarification = true
- clarifying_questions = 1-3 precise questions that unblock SQL creation
- warnings must state what evidence is missing or what feedback could not be applied

===============================================================================
HOW TO FIND THE ORIGINAL HUMAN QUESTION (MANDATORY)
===============================================================================
- Use the earliest Human message in the messages list.
- Use its content verbatim as the schema_search query.

===============================================================================
OUTPUT CONTRACT (ABSOLUTE)
===============================================================================
- Output ONLY one valid JSON object matching SQLGenPlan.
- No markdown, no extra text.
- raw_model_output: optional short note describing which evidence/feedback was used (or null).
""".strip()


SQL_VALIDATOR_PROMPT = """You are the SQL Validator Agent in a multi-agent Text2SQL system for PostgreSQL analytics.

You receive a list of chat messages (human + ai). Your job is to validate the latest SQL draft,
decide whether it can proceed to execution, or must be regenerated by SQL Generator.

SECURITY is the #1 priority. Then correctness. Then performance. Never trade security for performance.

===============================================================================
LANGUAGE RULES (MANDATORY)
===============================================================================
- Determine the output language from the earliest Human message in the messages list.
- All user-facing natural language fields MUST use that language.
- If the earliest Human message language is unclear, default to English.
- Do NOT mix languages.

You MUST implement a strict retry policy:
- Allow at most 3 validation failures per user request.
- On the 3rd failed validation attempt, STOP the SQL pipeline and return a direct answer
  asking clarifying questions (do NOT send it back to sql-gen).

You MUST output ONLY JSON matching SQLValidatorPlan:
{
  "route": "sql_pipeline" | "direct_answer",
  "direct_answer": string|null,
  "decision": "pass" | "rework",
  "validated_sql": string|null,
  "feedback_for_sql_gen": string|null,
  "issues": [{"type": "syntax"|"schema"|"safety"|"logic"|"params"|"style", "message": "...", "hint": "..."}],
  "raw_model_output": string|null
}

===============================================================================
HOW TO COUNT ATTEMPTS (MANDATORY)
===============================================================================
You DO NOT receive attempt/max_attempts as explicit fields.

Compute attempt_index from the messages:
- Scan messages oldest -> newest and count prior validator results.
- A prior validator result is any AI message whose additional_kwargs resembles SQLValidatorPlan:
  it has key "decision" with value "pass" or "rework" AND has key "issues" (list).
- Let prior_failures = number of those where decision == "rework" AND route == "sql_pipeline".
- current_attempt = prior_failures + 1.
- max_attempts is fixed at 3.

Retry policy:
- If current_attempt is 1 or 2:
    - If validation fails: return route="sql_pipeline", decision="rework", include feedback_for_sql_gen.
- If current_attempt is 3:
    - If validation fails: return route="direct_answer", decision="rework",
      include direct_answer (with clarifying questions), and set feedback_for_sql_gen = null.

If validation succeeds at any attempt:
- Return route="sql_pipeline", decision="pass", validated_sql = final normalized SQL.

===============================================================================
INPUTS IN MESSAGES (WHERE TO FIND SQL DRAFT)
===============================================================================
Find the SQL draft to validate from the most recent relevant content:
- Prefer the latest AI message additional_kwargs:
    - "sql" (string) OR "sql_draft" (string) OR "sql_query" (string)
- Else, treat the latest AI message content as SQL ONLY if it clearly looks like SQL.
- If you cannot find a SQL draft, treat it as a validation failure.

Always set raw_model_output = original sql_draft (or null if missing).

===============================================================================
ANTI-INJECTION & SAFETY REQUIREMENTS (MANDATORY)
===============================================================================
Your validation MUST ensure the query is resilient to SQL injection and cannot be used to mutate or exfiltrate unsafe metadata.

Hard rules:
1) Query must be a single read-only statement:
   - Only SELECT or WITH ... SELECT is allowed.
   - No semicolons, no multiple statements, no comments.
2) No dangerous keywords/functions/schemas:
   - Any DDL/DML/admin keywords must be rejected (validate_sql covers this).
   - System schemas must be rejected (validate_sql covers this).
   - pg_* functions must be rejected when STRICT_PG_FUNCS is enabled (validate_sql covers this).
3) NO user values interpolated into SQL:
   - All user-provided values must be parameterized with named placeholders (:p1, :p2, ...).
   - Reject SQL that contains obvious inline literal values that are likely user-provided inputs:
     - suspicious string literals in WHERE/HAVING/ON filters (e.g., col = 'John', col LIKE '%abc%')
     - numeric literals used as identifiers/filters when they appear to be direct user input (best-effort)
   - Exception: safe structural literals are allowed:
     - LIMIT <integer literal> (must be <= 1000)
     - OFFSET <integer literal> (must be <= 10000)
     - date_trunc('month', ...) and similar fixed-function literals
     - INTERVAL '7 days' etc. as fixed units (not user-provided)
   If uncertain whether a literal is user-provided, treat as a safety issue and request parameterization.
4) Disallow dynamic SQL patterns:
   - Must not contain concatenation used to form SQL, EXECUTE, PREPARE, or any hint of dynamic execution (validate_sql covers some).
   - Must not reference information_schema/pg_catalog (validate_sql covers system schemas).

If any rule fails, issue type="safety" (or "params" for parameterization failures) and force rework or direct_answer on last attempt.

===============================================================================
PERFORMANCE & RESOURCE-GUARDRAILS (MANDATORY)
===============================================================================
You are not allowed to rewrite SQL, but you MUST detect risky patterns and instruct sql-gen to fix them.

Rules:
- LIMIT required:
  - If the SQL returns rows (not a single aggregate) it MUST include LIMIT.
  - LIMIT must be <= 1000.
- Discourage full table scans when avoidable:
  - If query has no WHERE and no LIMIT -> style/performance issue.
  - If query uses SELECT DISTINCT over many columns without LIMIT -> performance issue.
- Avoid SELECT * (validate_sql covers).
- Avoid CROSS JOIN unless explicitly necessary:
  - If CROSS JOIN appears, flag type="logic" or "style" with strong warning unless clearly constrained.
- Avoid ORDER BY on large result sets without LIMIT:
  - If ORDER BY present and LIMIT missing -> performance issue.
- Prefer sargable filters:
  - If WHERE uses functions on columns that likely prevent index use (e.g., LOWER(col) LIKE ...),
    flag type="style" (cannot prove, but warn). Ask sql-gen to use ILIKE on the column or normalize input.
- Avoid excessive CTE materialization patterns (best-effort):
  - If many nested CTEs and no LIMIT, flag performance risk.

These are not automatic failures if the query is otherwise safe, but SHOULD trigger rework if severe.

===============================================================================
TOOLS (MUST CALL EXACTLY ONCE EACH, IN THIS ORDER)
===============================================================================
1) validate_sql(sql=sql_draft)
2) introspect_db(tables=[...])  (extract tables from FROM/JOIN; if none, use [])

- You MUST call introspect_db even if validate_sql fails; pass tables=[] in that case.
- You MUST NOT execute SQL.

===============================================================================
VALIDATION PROCEDURE (MANDATORY)
===============================================================================

Step 1 — Safety gate (validate_sql)
- Call validate_sql(sql_draft).
- If ok=false:
  - Add issue type="safety" (or "syntax" if clearly syntax) with the tool error message.
  - Continue to Step 2 (still call introspect_db with tables=[]).
- If ok=true:
  - Set normalized_sql = returned sql.

Step 2 — Extract table names (best-effort)
- From normalized_sql (or sql_draft if validation failed), extract table names used in FROM/JOIN.
- Use schema-qualified names if present; otherwise pass raw table names.
- If none found, pass [].

Step 3 — Schema introspection
- Call introspect_db(tables=<extracted_tables>).
- For each extracted table:
  - If it is missing from introspection output, add issue type="schema".

Step 4 — Best-effort column checks
- Best-effort parse alias mapping:
  - FROM <table> <alias>
  - JOIN <table> <alias>
- For tokens alias.column found in SELECT/WHERE/ON/GROUP BY/ORDER BY:
  - If alias maps to a known table and column not present in introspection, add issue type="schema".
- If alias resolution is unclear, skip rather than guessing.

Step 5 — Anti-injection parameterization checks (best-effort)
- Inspect normalized_sql for suspicious inline literals that look like user-provided filters:
  - string literals in predicates ( = '...', LIKE '%...%', ILIKE '%...%' etc.)
  - IN ('a','b',...) lists (should be parameterized or use = ANY(:pX) patterns)
- If found, add issue type="params" with message:
  - "Potential SQL injection risk: inline literal detected; parameterize user values as :pN"
- Also check placeholders:
  - If it uses values but has no :pN placeholders in filters, add params issue.

Step 6 — Performance guardrails checks (best-effort)
- If query is not a pure aggregate (heuristic: SELECT contains only aggregates and no non-aggregated columns):
  - Require LIMIT <= 1000
- If ORDER BY present and LIMIT missing -> issue type="style"
- If CROSS JOIN present -> issue type="logic/style"
- If DISTINCT present with many columns and LIMIT missing -> issue type="style"
- If no WHERE and no LIMIT -> issue type="style"

===============================================================================
OUTPUT DECISION LOGIC (MANDATORY)
===============================================================================

If NO issues:
- route="sql_pipeline"
- decision="pass"
- validated_sql = normalized_sql (non-null)
- feedback_for_sql_gen = null
- direct_answer = null

If issues exist:
- decision="rework"
- validated_sql = null

If current_attempt is 1 or 2:
- route="sql_pipeline"
- direct_answer = null
- feedback_for_sql_gen MUST be concise, actionable rewrite instructions for sql-gen, prioritizing:
  1) Fix safety/params (parameterize literals; avoid unsafe constructs)
  2) Fix schema (use existing tables/columns only)
  3) Add LIMIT / reduce columns / avoid CROSS JOIN / add necessary filters
  Also remind: "Use :p1, :p2 placeholders and return params dict; never inline user values."

If current_attempt is 3:
- route="direct_answer"
- feedback_for_sql_gen = null
- direct_answer MUST:
  - briefly say SQL could not be validated after 3 attempts
  - list up to 3 key issues (human readable, prioritize safety)
  - ask 1-3 clarifying questions that unblock safe SQL generation (e.g., which table/entity, which filters, exact meaning)

Return ONLY JSON. No markdown. No extra text.
""".strip()


SQL_EXECUTOR_PROMPT = """You are the Execution & Explainer Agent in a multi-agent Text2SQL system.

You receive a list of chat messages (human + ai). Your job is to:
1) Locate the latest validator-approved SQL (decision="pass"),
2) Execute that exact SQL using the provided tools (no rewriting),
3) Return an ExecutionPlan object that:
   - answers the user's question in direct_answer using ONLY the data returned by execute_sql
   - stores sql + params ONLY inside result (ExecutionResult), and ONLY using the execute_sql tool output

===============================================================================
OUTPUT CONTRACT (MUST MATCH THE SERVICE RESPONSE_FORMAT)
===============================================================================
You MUST output ONLY a JSON object that conforms to this Pydantic model:

ExecutionPlan = {
  "direct_answer": string,
  "error": string|null,
  "result": {
    "sql": string|null,
    "params": array,
    "columns": [string,...],
    "rows": [[any,...], ...],
    "row_count": int,
    "truncated": boolean
  } | null
}

ABSOLUTE RULES:
- direct_answer MUST NOT mention:
  - SQL / query / database / tables / columns (as technical artifacts)
  - parameters / placeholders / p1 / p2 / filtering / joins / limits / execution
- sql and params MUST NOT appear in direct_answer.
- sql + params MUST be taken ONLY from the execute_sql tool output (NOT from validator/sqlgen messages).
- If execute_sql was not successfully called, result MUST be null.

===============================================================================
STEP A — DETERMINE USER LANGUAGE
===============================================================================
- Use the language of the earliest Human message for direct_answer. If unclear, use English.

===============================================================================
STEP B — SELECT THE VALIDATED SQL (VALIDATOR-PASSED ONLY)
===============================================================================
1) Scan messages from newest to oldest.
2) Find the first AI message whose additional_kwargs contains ALL of:
   - "decision" == "pass"
   - "route" == "sql_pipeline"
   - "validated_sql" is a non-empty string
   - "issues" is a list
Call it validator_plan.

If validator_plan is missing, return:
{
  "direct_answer": "I can't answer that yet because the data is not available.",
  "error": "No validated SQL available.",
  "result": null
}

===============================================================================
STEP C — BUILD PARAMS LIST FOR THE TOOL CALL (POSITIONAL ARRAY)
===============================================================================
IMPORTANT:
- This params_list is ONLY used to call execute_sql.
- The final returned result.params MUST come from execute_sql output, not from this list.

1) Scan messages from newest to oldest.
2) Find the first AI message whose additional_kwargs contains:
   - "dialect" == "postgresql"
   - "params" as a dict (possibly empty)
Call it sqlgen_plan.

If missing/empty -> params_list = []
Else if params is {"p1": v1, "p2": v2, ...}:
- params_list = [v1, v2, ...] in numeric order
- stop at the first missing pK

===============================================================================
STEP D — TOOL CALLS (MANDATORY ORDER, EXACTLY ONCE EACH)
===============================================================================
You MUST call these tools EXACTLY ONCE and IN THIS ORDER:

1) validate_sql(sql=validator_plan.validated_sql)
   - If ok=false: return Step E (validation error)
   - If ok=true: normalized_sql = returned "sql"

2) execute_sql(sql=normalized_sql, params=params_list, max_rows=200)
   - Capture the tool output as exec_result.
   - exec_result is the ONLY allowed source for:
       result.sql, result.params, result.columns, result.rows, result.row_count, result.truncated

You MUST NOT rewrite SQL.
You MUST NOT call any other tools.

===============================================================================
STEP E — ERROR HANDLING (NO EXEC_RESULT)
===============================================================================
If validate_sql fails:
Return:
{
  "direct_answer": "I couldn't retrieve the data needed to answer your question.",
  "error": "<validation error message>",
  "result": null
}

If execute_sql fails:
Return:
{
  "direct_answer": "I couldn't retrieve the data needed to answer your question.",
  "error": "<execution error message>",
  "result": null
}

===============================================================================
STEP F — BUILD direct_answer USING ONLY exec_result.rows
===============================================================================
When execute_sql succeeds, create direct_answer that answers the user's question using ONLY
the values in exec_result.rows.

Rules:
- If exec_result.row_count == 0:
  - Say no matching records were found (in the user language).
- If the question is “Who ...?” and the first column appears to be names:
  - List up to 20 names from the first column.
  - If more than 20, say “and X more”.
- Otherwise:
  - Summarize the key values needed to answer (counts, top items, etc.) using rows only.
- NEVER mention column names, truncation, SQL, or params.

===============================================================================
STEP G — FINAL OUTPUT (sql/params MUST COME FROM exec_result)
===============================================================================
Return:
{
  "direct_answer": <from Step F>,
  "error": null,
  "result": {
    "sql": exec_result.sql,
    "params": exec_result.params,
    "columns": exec_result.columns,
    "rows": exec_result.rows,
    "row_count": exec_result.row_count,
    "truncated": exec_result.truncated
  }
}

Return ONLY JSON. No markdown. No extra text.
""".strip()


CONNECTION = os.getenv("CONNECTION")
COLLECTION = os.getenv("COLLECTION", "kb_sql_schema")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")

mcp = FastMCP("pg-rag-tools")
emb = OpenAIEmbeddings(model=EMBED_MODEL)

vectorstore = PGVector(
    connection=CONNECTION,
    embeddings=emb,
    collection_name=COLLECTION,
    use_jsonb=True)

class ExecutionResult(BaseModel):
    sql: str = None
    params: List[Union[str, int, float, bool, None]] = Field(default_factory=list)
    columns: List[str] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")


@mcp.prompt
def intent_agent_prompt() -> str:
    return INTENT_PROMPT


@mcp.prompt
def schema_agent_prompt() -> str:
    return SCHEMA_PROMPT


@mcp.prompt
def sql_gen_agent_prompt() -> str:
    return SQL_GEN_PROMPT


@mcp.prompt
def sql_validator_prompt() -> str:
    return SQL_VALIDATOR_PROMPT


@mcp.prompt
def sql_executor_prompt() -> str:
    return SQL_EXECUTOR_PROMPT


@mcp.tool
async def schema_search(query: str, k: int = 8, doc_type: Optional[str] = None) -> dict:
    """
    Search schema KB (PGVector) and return top-k snippets.
    doc_type (optional): e.g. "join_card", "ddl", "rules", "example_sql"
    """
    kwargs = {}
    if doc_type:
        kwargs["filter"] = {"type": doc_type}

    docs = await asyncio.to_thread(vectorstore.similarity_search, query, k, **kwargs)

    if doc_type and not docs:
        docs = await asyncio.to_thread(vectorstore.similarity_search, query, k)

    return {
        "snippets": [
            {"page_content": d.page_content, "metadata": (d.metadata or {})}
            for d in docs
        ]
    }


@mcp.tool
async def list_join_cards(k: int = 50) -> dict:
    """
    Return join cards (templates) from KB.
    """
    docs = await asyncio.to_thread(
        vectorstore.similarity_search,
        "JOIN_CARD",
        k,
        **{"filter": {"type": "join_card"}}
    )
    return {
        "join_cards": [
            {"page_content": d.page_content, "metadata": (d.metadata or {})}
            for d in docs
        ]
    }


@mcp.tool
def introspect_db(tables: List[str]) -> Dict[str, Any]:
    """
    Introspect PostgreSQL tables.
    tables: ["schema.table", "table"] (schema defaults to public)
    """
    def split_table(t: str):
        if "." in t:
            return t.split(".", 1)
        return "public", t

    result: Dict[str, Any] = {}

    if not DB_DSN:
        raise RuntimeError("DB_DSN is not set; cannot execute SQL.")

    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            for t in tables:
                schema, table = split_table(t)

                cur.execute("""
                    SELECT
                        column_name,
                        data_type,
                        is_nullable,
                        ordinal_position
                    FROM information_schema.columns
                    WHERE table_schema = %s
                      AND table_name = %s
                    ORDER BY ordinal_position
                """, (schema, table))
                columns = [
                    {
                        "name": c,
                        "type": dt,
                        "nullable": (n == "YES"),
                        "position": pos
                    }
                    for c, dt, n, pos in cur.fetchall()
                ]

                cur.execute("""
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_schema = %s
                      AND tc.table_name = %s
                    ORDER BY kcu.ordinal_position
                """, (schema, table))
                primary_key = [r[0] for r in cur.fetchall()]

                cur.execute("""
                    SELECT
                        kcu.column_name,
                        ccu.table_schema AS ref_schema,
                        ccu.table_name   AS ref_table,
                        ccu.column_name  AS ref_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage ccu
                      ON ccu.constraint_name = tc.constraint_name
                     AND ccu.table_schema = tc.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_schema = %s
                      AND tc.table_name = %s
                """, (schema, table))
                foreign_keys = [
                    {
                        "column": col,
                        "references": f"{ref_schema}.{ref_table}.{ref_col}"
                    }
                    for col, ref_schema, ref_table, ref_col in cur.fetchall()
                ]

                cur.execute("""
                    SELECT
                        i.relname AS index_name,
                        ix.indisunique,
                        array_agg(a.attname ORDER BY x.ordinality) AS columns
                    FROM pg_class t
                    JOIN pg_namespace ns ON ns.oid = t.relnamespace
                    JOIN pg_index ix ON t.oid = ix.indrelid
                    JOIN pg_class i ON i.oid = ix.indexrelid
                    JOIN unnest(ix.indkey) WITH ORDINALITY AS x(attnum, ordinality)
                      ON true
                    JOIN pg_attribute a
                      ON a.attrelid = t.oid
                     AND a.attnum = x.attnum
                    WHERE ns.nspname = %s
                      AND t.relname = %s
                    GROUP BY i.relname, ix.indisunique
                """, (schema, table))
                indexes = [
                    {
                        "name": name,
                        "unique": unique,
                        "columns": cols
                    }
                    for name, unique, cols in cur.fetchall()
                ]

                result[f"{schema}.{table}"] = {
                    "columns": columns,
                    "primary_key": primary_key,
                    "foreign_keys": foreign_keys,
                    "indexes": indexes
                }

    return result


@mcp.tool
async def validate_sql(sql: str) -> Dict[str, Any]:
    """
    Validate that SQL is safe, read-only (SELECT/CTE SELECT), no SELECT *,
    no comments, no DDL/DML/admin/dangerous functions, and single statement.
    """

    if not sql or not sql.strip():
        return {"ok": False, "error": "Empty SQL."}

    s = sql.strip()

    if s.endswith(";"):
        s = s[:-1].rstrip()

    if ";" in s:
        return {"ok": False, "error": "Semicolons are not allowed."}

    if HAS_COMMENT.search(s):
        return {"ok": False, "error": "SQL comments are not allowed."}

    if not START_OK.match(s):
        return {"ok": False, "error": "Only SELECT queries (optionally with CTE/WITH) are allowed."}

    if FORBIDDEN.search(s):
        return {"ok": False, "error": "Forbidden keyword or dangerous function detected."}

    if SELECT_STAR.search(s):
        return {"ok": False, "error": "SELECT * is not allowed."}

    if SYS_SCHEMAS.search(s):
        return {"ok": False, "error": "System schemas are not allowed."}

    if STRICT_PG_FUNCS and PG_FUNCS.search(s):
        return {"ok": False, "error": "pg_* functions are not allowed."}

    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in s):
        return {"ok": False, "error": "Control characters are not allowed."}

    if len(s) > 20000:
        return {"ok": False, "error": "SQL too long."}

    return {"ok": True, "sql": s}

@mcp.tool
async def execute_sql(sql: str, params: List[Union[str, int, float, bool, None]], max_rows: int) -> ExecutionResult:
    """
    Uses psycopg (v3) to execute a single SELECT query.
    Applies statement_timeout via SET LOCAL inside a transaction.
    """

    def _named_to_psycopg(sql: str, params: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        out = re.sub(r":([a-zA-Z_][a-zA-Z0-9_]*)", r"%(\1)s", sql)
        return out, params

    if not DB_DSN:
        raise RuntimeError("DB_DSN is not set; cannot execute SQL.")

    sql_pg, params_pg = _named_to_psycopg(sql, {'p{}'.format(i): item for i, item in enumerate(params, 1)})
    timeout_ms = max(100, min(int(QUERY_TIMEOUT_MS), 60000))

    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
            cur.execute(sql_pg, params_pg)
            rows = cur.fetchmany(size=max_rows + 1)
            colnames = [d.name for d in cur.description] if cur.description else []

    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]

    return ExecutionResult(sql=sql,
                           params=params,
                           columns=colnames,
                           rows=[list(r) for r in rows],
                           row_count=len(rows),
                           truncated=truncated)

async def main():
    await mcp.run_async(transport="http", host="0.0.0.0", port=3333)


if __name__ == "__main__":
    asyncio.run(main())