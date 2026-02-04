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
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from pydantic import BaseModel, Field
from typing import Any, List, Literal, Optional, Dict

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

class SQLGenPlan(BaseModel):
    dialect: Literal["postgresql"] = "postgresql"
    sql: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarifying_questions: List[str] = Field(default_factory=list)
    raw_model_output: Optional[str] = None

async def append_structured_content(request: MCPToolCallRequest, handler):
    result = await handler(request)
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
    prompt = await MCP_CLIENT.get_prompt('pg_rag', 'sql_gen_agent_prompt')
    tools = await MCP_CLIENT.get_tools()
    agent = create_agent(model=LLM,
                         tools=tools,
                         system_prompt=prompt[0].content,
                         response_format=SQLGenPlan,
                         debug=DEBUG)
    yield
    agent = None

app = FastAPI(title="SQL Gen Agent Service", 
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
        result = await agent.ainvoke({"messages": [message.to_message() for message in messages]})
        return result["structured_response"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent invocation failed: {e}")
