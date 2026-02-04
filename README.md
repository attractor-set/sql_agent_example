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

## 🧱 Diagrams

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
```

```mermaid
flowchart LR

U[User]
UI[Streamlit Frontend]
J[Jaeger UI]

subgraph S[System: SQL Agent Example]
  API[API / LangGraph Orchestrator]

  P1[Intent Agent]
  P2[Schema Agent]
  P3[SQL Generation Agent]
  P4[SQL Validator Agent]
  P5[SQL Executor Agent]
  P6[Final Node]

  D1[(Checkpointer DB)]
  D2[(PostgreSQL DB)]
  D3[(pgvector RAG Store)]

  MCP[MCP Server]
end

U --> UI
UI --> API

API --> P1
P1 --> API

API --> P6
P6 --> API
API --> UI
UI --> U

API --> P2
P2 --> API

API --> P3
P3 --> API

API --> P4
P4 --> API

API --> P3

API --> P5
P5 --> API

API --> D1
D1 --> API

P2 --> MCP
MCP --> D3
D3 --> MCP

P5 --> MCP
MCP --> D2
D2 --> MCP

API --> J
P1 --> J
P2 --> J
P3 --> J
P4 --> J
P5 --> J
```

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
