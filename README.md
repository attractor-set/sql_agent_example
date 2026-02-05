# 🧠 SQL Agent Example  
**End-to-End Multi-Agent Text-to-SQL System with LangGraph, MCP, RAG, OpenTelemetry & Streamlit**

This project demonstrates a **fully containerized, observable, multi-agent Text-to-SQL architecture**, built around **LangGraph**, **FastAPI**, **PostgreSQL + pgvector**, and **OpenTelemetry**.

Natural-language questions are routed through specialized agents (Intent, Schema, SQL Generation, Validation, Execution) and executed safely against a relational database — with **full distributed tracing via Jaeger** and a **Streamlit chat UI**.

---

## ✨ Key Features

- 🧩 **Multi-Agent Architecture**
  - Intent classification  
  - Schema understanding  
  - SQL generation  
  - SQL validation & retries  
  - Secure SQL execution  

- 🧠 **LangGraph State Machine**
  - Conditional routing  
  - Retry loops  
  - Persistent state via PostgreSQL checkpointer  

- 🧬 **RAG over database schema and metadata (pgvector)**
  - pgvector + embeddings  
  - Schema and metadata retrieval via MCP server  

- 🔍 **Full Observability**
  - OpenTelemetry spans  
  - Jaeger UI with end-to-end traces  
  - Agent-level latency & routing visibility

  <p align="center">
    <img src="png/opentelemetry.png" alt="Jaeger Traces" width="900">
  </p>

- 🖥 **Streamlit Frontend**
  - Chat interface  
  - SQL preview & parameters  
  - Query result inspection

  <p align="center">
    <img src="png/streamlit.png" alt="Streamlit Chat UI" width="900">
  </p>

- 🐳 **100% Dockerized**
  - One-command startup  
  - Health-checked service dependencies  

  <p align="center">
    <img src="png/docker.png" alt="Docker Services" width="900">
  </p>

---

## 🧱 UML Class Diagram

```mermaid
classDiagram
direction LR

class GraphState {
  +messages: List~AnyMessage~
  +history: List~AnyMessage~ <<add_messages>>
}

class AnyMessage {
  +content: Any
  +name: str?
  +additional_kwargs: dict
}
class HumanMessage
class AIMessage
AnyMessage <|-- HumanMessage
AnyMessage <|-- AIMessage

GraphState "1" o-- "*" AnyMessage : messages
GraphState "1" o-- "*" AnyMessage : history

class StateGraph {
  +add_node(name: str, fn: Callable)
  +add_edge(src: str, dst: str)
  +add_conditional_edges(src: str, router: Callable, mapping: dict)
  +compile(checkpointer)
}

class OrchestratorAPI {
  +POST /chat(content, thread_id)
  +invoke(graph, state)
}

class Checkpointer {
  +load(thread_id) GraphState
  +save(thread_id, state)
}

class Postgres {
  +appdb
  +pgvector extension
  +checkpoint tables
}

class MCPServer {
  +tools: schema_search
  +tools: introspect_db
  +tools: list_join_cards
  +tools: validate_sql
  +tools: execute_sql
  +prompts: intent/schema/sqlgen/validator/executor
}

class AgentRuntime {
  +POST /messages(List~AnyMessage~)
  +Authorization: Bearer token
  +LLM call
  +MCP tool/prompt calls
}

class IntentAgent
class SchemaAgent
class SQLGenAgent
class SQLValidatorAgent
class SQLExecutorAgent
AgentRuntime <|-- IntentAgent
AgentRuntime <|-- SchemaAgent
AgentRuntime <|-- SQLGenAgent
AgentRuntime <|-- SQLValidatorAgent
AgentRuntime <|-- SQLExecutorAgent

class NodeFn {
  +call(state: GraphState) GraphState
}

class RouterFn {
  +call(state: GraphState) route: str
}

class intent_node
class schema_node
class sqlgen_node
class validator_node
class executor_node
class final_node

NodeFn <|.. intent_node
NodeFn <|.. schema_node
NodeFn <|.. sqlgen_node
NodeFn <|.. validator_node
NodeFn <|.. executor_node
NodeFn <|.. final_node

class intent_route
class validate_route
RouterFn <|.. intent_route
RouterFn <|.. validate_route

OrchestratorAPI --> StateGraph : builds/invokes
OrchestratorAPI --> Checkpointer : uses
Checkpointer --> Postgres : persists

StateGraph --> GraphState : state type
StateGraph --> NodeFn : nodes
StateGraph --> RouterFn : routers

intent_node --> IntentAgent : HTTP call
schema_node --> SchemaAgent : HTTP call
sqlgen_node --> SQLGenAgent : HTTP call
validator_node --> SQLValidatorAgent : HTTP call
executor_node --> SQLExecutorAgent : HTTP call

AgentRuntime --> MCPServer : tool/prompt calls
MCPServer --> Postgres : queries
```

## 🧱 UML Sequence Diagram

```mermaid
sequenceDiagram
autonumber
actor U as User
participant UI as Streamlit
participant API as API
participant IA as IntentAgent
participant SA as SchemaAgent
participant GA as SQLGenAgent
participant VA as ValidatorAgent
participant EA as ExecutorAgent
participant MCP as MCP
participant PG as Postgres
participant CP as Checkpointer

Note over API,EA: All agent calls include Authorization: Bearer <token>

U->>UI: Ask question
UI->>API: POST chat
API->>CP: Load state
CP-->>API: State

API->>IA: Messages
IA-->>API: Route

alt Route is final
  API-->>UI: Direct answer
else Route is schema
  API->>SA: Messages
  SA->>MCP: Schema tools
  MCP->>PG: Query
  PG-->>MCP: Rows
  MCP-->>SA: Result
  SA-->>API: Schema context

  API->>GA: Messages
  GA-->>API: SQL draft

  loop Validation attempts
    API->>VA: SQL draft
    VA->>MCP: validate_sql
    MCP-->>VA: Result
    VA-->>API: Outcome

    alt Outcome is rework
      API->>GA: Feedback
      GA-->>API: Revised SQL
    else Outcome is done
      API->>EA: Validated SQL
      EA->>MCP: execute_sql
      MCP->>PG: SELECT
      PG-->>MCP: Rows
      MCP-->>EA: Result
      EA-->>API: Answer
      API-->>UI: Final answer
    end
  end
end
```

## 🧱 Data Flow Diagram (DFD)

```mermaid
flowchart LR

User[User] --> UI[Streamlit Frontend] --> API[API or LangGraph Orchestrator]

subgraph LANGGRAPH[LangGraph pipeline]
  Intent[Intent node]
  Schema[Schema node]
  SQLGen[SQL generator node]
  Validator[SQL validator node]
  Executor[SQL executor node]
  Final[Final node]
end

API -->|Bearer Auth| Intent

Intent -->|route: schema| Schema
Intent -->|route: final| Final

Schema --> SQLGen --> Validator

Validator -->|route: execute| Executor
Validator -->|route: sqlgen rework| SQLGen
Validator -->|route: final fallback| Final

Executor --> Final --> API

Checkpoint[(LangGraph checkpointer in Postgres)]
API <--> Checkpoint

MCP[MCP server tools and prompts]
PG[(PostgreSQL appdb with pgvector)]

Schema --> MCP
SQLGen --> MCP
Validator --> MCP
Executor --> MCP
MCP --> PG
Checkpoint --- PG

Jaeger[Jaeger tracing UI]
API --> Jaeger
```

---

## 🏗 Architecture Overview

```
User (Streamlit)
   ↓
API (LangGraph Orchestrator)
   ↓
Intent Agent
   ├──→ Final Answer (direct response)
   └──→ Schema Agent
           ↓
       SQL Generator Agent
           ↓
       SQL Validator Agent
           ├──→ SQL Generator (rework loop)
           ├──→ Final Answer (fallback)
           └──→ SQL Executor Agent
                   ↓
               Final Answer + Trace
```
---

## 🔐 Service-to-Service Authentication (Bearer Token)

Communication between the **Orchestrator API** and all **Agent services** is protected using **HTTP Bearer Token authentication**.

### How it works

- A shared secret token (`AGENT_API_TOKEN`) is configured via environment variables
- The **Orchestrator** includes the token in every agent request: Authorization: Bearer <AGENT_API_TOKEN>
- Each **Agent Runtime** validates the token before processing `/messages`
- Requests without a valid token are rejected with `401 Unauthorized`

This ensures that:
- Only the orchestrator can invoke agents
- Agents are not callable by external clients
- The system remains secure even when deployed across networks

### Scope

- Authentication applies **only to internal service-to-service calls**
- End-user authentication (UI → API) is intentionally out of scope

### Configuration

```env
AGENT_API_TOKEN=super-long-random-secret
```

The same token must be provided to:

- api (orchestrator)

- all agent containers (intent-agent, schema-agent, etc.)

This is **concise, explicit, and production-grade**.

---

## 📁 Project Structure

```
sql_agent_example-main/
│
├── api/                     # LangGraph orchestrator (FastAPI)
│   ├── app/
│   │   ├── graph.py         # StateGraph + routing logic
│   │   └── main.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── agent-gen/               # Generic agent runtime (all agents share this image)
│   ├── main.py              # FastAPI /messages endpoint + LLM invocation
│   ├── Dockerfile
│   └── requirements.txt
│
├── mcp-server/              # MCP tools + prompts (schema/RAG/validation/execution)
│   ├── mcp_server.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── rag-init/                # Vector store bootstrap (pgvector KB seeding)
│   ├── rag_setup.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── streamlit-frontend/      # Chat UI
│   ├── main.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── sql/
│   ├── 00_schema.sql         # Sample schema
│   └── 01_extensions.sql     # pgvector, extensions
│
├── png/                      # README assets
│   ├── opentelemetry.png
│   ├── streamlit.png
│   └── docker.png
│
├── docker-compose.yml
├── .env_example
└── README.md
```

---

## ⚙️ Services & Ports

| Service | Description | Port |
|------|------------|------|
| PostgreSQL + pgvector | Database | `5432` |
| MCP Server | Schema + RAG tools | `3333` |
| Intent Agent | Intent routing | `8001` |
| Schema Agent | Schema reasoning | `8002` |
| SQL Gen Agent | SQL drafting | `8003` |
| SQL Validator | Validation & retry | `8004` |
| SQL Executor | Query execution | `8005` |
| API Orchestrator | LangGraph | `8000` |
| Streamlit UI | Chat frontend | `8501` |
| Jaeger UI | Tracing | `16686` |

---

## 🚀 Running the Project

### 1️⃣ Configure Environment

```bash
cp .env_example .env
```

Set at least:

```env
OPENAI_API_KEY=sk-...
```

---

### 2️⃣ Start Everything

```bash
docker compose up --build
```

All services include **health checks** and start in the correct order.

---

### 3️⃣ Open the UIs

- 💬 **Chat UI (Streamlit)**  
  http://localhost:8501

- 🔍 **Jaeger Tracing UI**  
  http://localhost:16686

---

## 🔐 Safety & Guardrails

- ❌ No DDL / destructive SQL  
- ❌ No multi-statement execution  
- ✅ Parameterized queries  
- ✅ Validator retry limits  
- ✅ Clear fallback to direct answers
- ✅ Statement timeout enforcement
- ✅ Result row truncation
- 🔐 Authenticated agent access via Bearer token (orchestrator → agents)

---

## 📜 License

MIT — do whatever you want, just don’t blame the agents 😉
