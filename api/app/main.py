import os
from app.graph import gen_graph
from typing import Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
from uuid import uuid4

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pg-rag-tools")

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception as _:
    pass

DB_DSN = os.getenv("DB_DSN")

graph: Any = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph
    async with AsyncPostgresSaver.from_conn_string(DB_DSN) as checkpoiter:
        await checkpoiter.setup()
        graph = gen_graph(checkpoiter)
        yield

    graph = None

app = FastAPI(title="Agent Service API", 
              version="1.0.0",
              lifespan=lifespan)

class Message(BaseModel):
    content: str = ""
    thread_id: str = Field(default_factory=lambda: str(uuid4()))


    def to_message(self) -> HumanMessage:
        return HumanMessage(content=self.content, additional_kwargs={"thread_id": self.thread_id})
    

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(message: Message):
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph is not initialized")
    
    try:
        result = await graph.ainvoke({"messages": [message.to_message()]},
                                     {"configurable": {"thread_id": message.thread_id}})
        msg = result['messages'][-1]
        msg.additional_kwargs.update({"thread_id": message.thread_id})
        return msg
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"Graph invocation failed: {traceback.format_exc()}")
