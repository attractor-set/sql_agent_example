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
  
  ```mermaid
  classDiagram
  direction LR

  class GraphState {
    +messages: List~AnyMessage~
    +history: List~AnyMessage~
  }

  class BaseMessage {
    +content: Any
    +name: str
    +additional_kwargs: dict
  }

  class HumanMessage
  class AIMessage

  BaseMessage <|-- HumanMessage
  BaseMessage <|-- AIMessage

  GraphState "1" o-- "*" BaseMessage : messages/history

  class Orchestrator {
    +__call__(state)
  }

  class IntentAgent
  class SchemaAgent
  class SQLGenAgent
  class SQLValidatorAgent
  class SQLExecutorAgent

  Orchestrator --> IntentAgent
  Orchestrator --> SchemaAgent
  Orchestrator --> SQLGenAgent
  Orchestrator --> SQLValidatorAgent
  Orchestrator --> SQLExecutorAgent

  GraphState --> Orchestrator


- 🧠 **LangGraph State Machine**
  - Conditional routing  
  - Retry loops  
  - Persistent state via PostgreSQL checkpointer  
  
  ```mermaid
  flowchart LR
  %% External Entities
  U[/"User"/]
  UI[/"Streamlit Frontend"/]
  J[/"Jaeger UI (Tracing)"/]

  %% System Boundary
  subgraph S["System: SQL Agent Example"]
    API["P0 API / LangGraph Orchestrator<br/>(FastAPI + StateGraph)"]

    P1["P1 Intent Agent<br/>(intent classification & routing)"]
    P2["P2 Schema Agent<br/>(schema understanding)"]
    P3["P3 SQL Gen Agent<br/>(generate SQL draft)"]
    P4["P4 SQL Validator Agent<br/>(validation & retry policy)"]
    P5["P5 SQL Executor Agent<br/>(execute SQL & format result)"]
    P6["P6 Final Node<br/>(compact history & response)"]

    D1[("D1 Checkpointer (PostgreSQL)<br/>state & history")]
    D2[("D2 PostgreSQL DB<br/>business tables")]
    D3[("D3 pgvector / RAG Store<br/>schema docs & embeddings")]

    MCP["MCP Server<br/>(schema lookup & SQL tools)"]
  end

  %% Entry
  U -->|NL question| UI
  UI -->|POST /chat<br/>messages + thread_id| API

  %% Intent branching
  API -->|messages| P1
  P1 -->|route decision| API

  %% Short path (direct.png)
  API -->|if direct_answer| P6
  P6 -->|final response| API
  API -->|Answer (NL)| UI
  UI -->|Display answer| U

  %% Long path (complete.png)
  API -->|if sql_pipeline| P2
  P2 -->|schema context| API

  API -->|messages + schema| P3
  P3 -->|SQL draft + params| API

  API -->|SQL + policy| P4
  P4 -->|pass / rework + feedback| API

  %% Retry loop
  API -->|if rework| P3

  API -->|if pass| P5
  P5 -->|rows + summary| API

  API -->|finalize| P6

  %% Data stores
  API <--> |read/write| D1

  P2 -->|schema lookup| MCP
  MCP <--> |embeddings| D3

  P5 -->|execute_sql| MCP
  MCP <--> |SELECT| D2

  %% Observability
  API -->|OTEL spans| J
  P1 -->|OTEL spans| J
  P2 -->|OTEL spans| J
  P3 -->|OTEL spans| J
  P4 -->|OTEL spans| J
  P5 -->|OTEL spans| J


- 🧬 **RAG over Database Schema**
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

## 🏗 Architecture Overview

```
User (Streamlit)
   ↓
API (LangGraph Orchestrator)
   ↓
Intent Agent
   ↓
Schema Agent
   ↓
SQL Generator Agent
   ↓
SQL Validator Agent (retry loop)
   ↓
SQL Executor Agent
   ↓
Final Answer + Trace
```

---

## 📁 Project Structure

```
sql_agent_example/
│
├── api/                     # LangGraph orchestrator (FastAPI)
│   ├── app/
│   │   ├── graph.py         # StateGraph + routing logic
│   │   └── main.py
│   └── Dockerfile
│
├── intent-agent/             # Intent classification agent
├── schema-agent/             # DB schema understanding agent
├── sql-gen-agent/            # SQL generation agent
├── sql-validator-agent/      # SQL validation + retry policy
├── sql-executor-agent/       # Secure SQL execution agent
│
├── mcp-server/               # MCP tools + schema/RAG access
├── rag-init/                 # Vector store bootstrap
│
├── streamlit-frontend/       # Chat UI
│
├── sql/
│   ├── 00_schema.sql         # Sample schema
│   └── 01_extensions.sql     # pgvector, extensions
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

---

## 📜 License

MIT — do whatever you want, just don’t blame the agents 😉
