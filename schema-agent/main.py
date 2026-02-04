import os
import json
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from typing import Any, List
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import TextContent
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pg-rag-tools")

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception as _:
    pass

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.1")
DEBUG = os.getenv("DEBUG", "false") == "true"
LLM = ChatOpenAI(model=LLM_MODEL, temperature=0)

class Message(BaseModel):
    role: Literal["human", "ai"]
    content: Optional[str] = ""
    additional_kwargs: Optional[dict] = Field(default_factory=dict)
    name: Optional[str] = ""


    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "additional_kwargs": self.additional_kwargs,
            "name": self.name
        }


    def to_message(self) -> BaseMessage:
        return HumanMessage(content=self.content, name=self.name, additional_kwargs=self.additional_kwargs) \
               if self.role in ["human"] else \
               AIMessage(content=self.content if self.content else json.dumps(self.additional_kwargs), name=self.name, additional_kwargs=self.additional_kwargs)

class AggregationPlan(BaseModel):
    metric_expression_hint: str
    group_by_hints: List[str] = Field(default_factory=list)
    time_bucket_hint: Optional[str] = None
    notes: Optional[str] = None

class FilterMapping(BaseModel):
    original_field_hint: str
    mapped_table: Optional[str] = None
    mapped_column: Optional[str] = None
    operator: Literal["=", "!=", ">", ">=", "<", "<=", "in", "between", "like"]
    value: Union[str, int, float, List[Any], Dict[str, Any]]
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    notes: Optional[str] = None

class FieldRef(BaseModel):
    table: str
    column: str
    role: Literal["metric", "dimension", "filter", "time", "sort", "other"]
    semantic_hint: Optional[str] = None

class JoinEdge(BaseModel):
    left_table: str
    right_table: str
    join_type: Literal["inner", "left", "right", "full"] = "inner"
    on: List[Dict[str, str]] = Field(default_factory=list)
    notes: Optional[str] = None

class SchemaPlan(BaseModel):
    primary_tables: List[str] = Field(default_factory=list)
    required_fields: List[FieldRef] = Field(default_factory=list)
    join_path: List[JoinEdge] = Field(default_factory=list)
    filter_mappings: List[FilterMapping] = Field(default_factory=list)
    aggregation: Optional[AggregationPlan] = None
    assumptions: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarifying_questions: List[str] = Field(default_factory=list)

async def append_structured_content(request: MCPToolCallRequest, handler):
    result = await handler(request)
    print(result.structuredContent)
    if result.structuredContent:
        result.content += [
            TextContent(type="text", text=json.dumps(result.structuredContent)),
        ]
    return result


MCP_CLIENT = MultiServerMCPClient({"pg_rag": {"transport": "http",
                                              "url": os.getenv("MCP_URL", "http://mcp-server:3333/mcp")}},
                                  tool_interceptors=[append_structured_content])

agent: Any = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent
    prompt = await MCP_CLIENT.get_prompt('pg_rag', 'schema_agent_prompt')
    tools = await MCP_CLIENT.get_tools()
    agent = create_agent(model=LLM,
                         tools=tools,
                         system_prompt=prompt[0].content,
                         response_format=SchemaPlan,
                         debug=DEBUG)
    yield
    agent = None

app = FastAPI(title="Schema Agent Service", 
              version="1.0.0",
              lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/messages")
async def messages(messages: List[Message]):
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent is not initialized")

    try:
        payload = {"messages": [m.to_message() for m in messages]}
        for msg in payload["messages"]:
            logger.info("MSG type=%s name=%s content=%r", type(msg).__name__, getattr(msg, "name", None), getattr(msg, "content", None))

        result = await agent.ainvoke(payload)
        return result["structured_response"]
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"Agent invocation failed: {traceback.format_exception(e)}")
