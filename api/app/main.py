import os
from app.graph import gen_graph
import traceback
from typing import Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
from uuid import uuid4

from opentelemetry.trace import Status, StatusCode, get_tracer_provider, get_tracer, set_tracer_provider
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.resources import Resource

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pg-rag-tools")

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception as _:
    pass

DB_DSN = os.getenv("DB_DSN")
OTLP_ENDPOINT = os.getenv("OTEL_OTLP_ENDPOINT", "http://jaeger:4317")

graph: Any = None
tracer: Any = None


def setup_tracing():
    resource = Resource.create(attributes={"service.name": "agent-service",
                                           "service.namespace": "pg-rag-tools",
                                           "service.version": "1.0.0",
                                           "deployment.environment": os.getenv("ENV", "DEV"),})
    
    set_tracer_provider(TracerProvider(resource=resource))
    tracer_provider = get_tracer_provider()
    
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)))
    tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    RequestsInstrumentor().instrument()
    return get_tracer(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph, tracer
    tracer = setup_tracing()    
    async with AsyncPostgresSaver.from_conn_string(DB_DSN) as checkpoiter:
        await checkpoiter.setup()
        graph = gen_graph(checkpoiter)
        FastAPIInstrumentor.instrument_app(app, tracer_provider=get_tracer_provider())
        logger.info("Application started with OpenTelemetry tracing enabled")
        yield

    graph = None
    tracer = None

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
    
    with tracer.start_as_current_span("chat_endpoint") as span:
        span.set_attributes({"thread_id": message.thread_id,
                             "message.length": len(message.content),
                             "endpoint": "/chat"})
        try:
            result = await graph.ainvoke({"messages": [message.to_message()]},
                                             {"configurable": {"thread_id": message.thread_id}})
                
            msg = result['messages'][-1]
            msg.additional_kwargs.update({"thread_id": message.thread_id})
                
            span.set_status(Status(StatusCode.OK))
            return msg
            
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            
            logger.error(f"Graph invocation failed: {traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"Graph invocation failed: {str(e)}")
