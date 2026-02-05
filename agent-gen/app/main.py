import os
import json
from uuid import uuid4
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from typing import Any, List
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import TextContent
from typing import Any, List, Literal, Optional
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from app.auth import require_agent_token

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
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT")
OUTPUT_SCHEMA = os.getenv("OUTPUT_SCHEMA")
USE_TOOLS = os.getenv("USE_TOOLS", "false") == "true"
AGENT_TITLE = os.getenv("AGENT_TITLE", str(uuid4()))

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

LLM = ChatOpenAI(model=LLM_MODEL, temperature=0)

agent: Any = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent
    prompt = await MCP_CLIENT.get_prompt('pg_rag', SYSTEM_PROMPT)
    params = dict(model=LLM,
                  system_prompt=prompt[0].content,
                  debug=DEBUG)
    
    if OUTPUT_SCHEMA:
        params["response_format"] = ProviderStrategy(json.loads(OUTPUT_SCHEMA))
    
    if USE_TOOLS:
        params["tools"] = await MCP_CLIENT.get_tools()
    
    agent = create_agent(**params)
    yield
    agent = None

app = FastAPI(title=AGENT_TITLE, 
              version="1.0.0",
              lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/messages")
async def messages(messages: List[Message], dependencies=[Depends(require_agent_token)]):
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent is not initialized")

    try:

        payload = {"messages": [m.to_message() for m in messages]}
        for msg in payload["messages"]:
            logger.info("MSG type=%s name=%s content=%r", type(msg).__name__, getattr(msg, "name", None), getattr(msg, "content", None))

        result = await agent.ainvoke(payload)
        return result["structured_response"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent invocation failed: {e}")


