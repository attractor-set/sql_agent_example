import requests
from typing import Any, Dict, List, TypedDict, Annotated

from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, AnyMessage
from langgraph.graph.message import add_messages
from langgraph.graph.state import END, StateGraph


class GraphState(TypedDict):
    messages: List[AnyMessage]
    history: Annotated[list[AnyMessage], add_messages]


def make_agent_node(step_name: str, agent_url: str):
    def call_agent(base_url: str, messages: List[BaseMessage]) -> Dict[str, Any]:
        def msg2dict(m: BaseMessage) -> Dict[str, Any]:
            return {"role": "human" if isinstance(m, HumanMessage) else "ai",
                    "content": m.content,
                    "name": getattr(m, "name", None),
                    "additional_kwargs": getattr(m, "additional_kwargs", {}),}
        
        resp = requests.post(f"{base_url}/messages",
                            json=[msg2dict(m) for m in messages],
                            timeout=30)
        
        resp.raise_for_status()
        return resp.json()

    def gen_node(state: GraphState) -> GraphState:
        structured_result = call_agent(agent_url, state['history'] + state["messages"])
        return {"messages": state["messages"] + [AIMessage(content=structured_result.get("direct_answer") or "", 
                                                           additional_kwargs=structured_result, 
                                                           name=step_name)]}
    
    return gen_node


intent_node   = make_agent_node("intent", "http://intent-agent:8001")
schema_node   = make_agent_node("schema", "http://schema-agent:8002")
sqlgen_node   = make_agent_node("sqlgen", "http://sql-gen-agent:8003")
validator_node = make_agent_node("validate", "http://sql-validator-agent:8004")
executor_node  = make_agent_node("execute", "http://sql-executor-agent:8005")


def intent_route(state: GraphState) -> str:
    last_msg = state["messages"][-1]
    return "final" if last_msg.additional_kwargs.get("route", "sql_pipeline") == "direct_answer" else "schema"


def validate_route(state: GraphState) -> str:
    last_msg = state["messages"][-1]
    return "execute" if last_msg.additional_kwargs.get("decision", "pass") == "pass" else "sqlgen" if last_msg.additional_kwargs.get("route", "sql_pipeline") == "sql_pipeline" else "final"


def final_node(state: GraphState) -> GraphState:
    return {'history': [state["messages"][0], state["messages"][-1]]}


def gen_graph(checkpointer):
    builder = StateGraph(GraphState)

    builder.add_node("intent", intent_node)
    builder.add_node("schema", schema_node)
    builder.add_node("sqlgen", sqlgen_node)
    builder.add_node("validate", validator_node)
    builder.add_node("execute", executor_node)
    builder.add_node("final", final_node)

    builder.add_edge("schema", "sqlgen")
    builder.add_edge("sqlgen", "validate")
    builder.add_edge("execute", "final")
    builder.add_edge("final", END)

    builder.add_conditional_edges("intent", intent_route, {"schema": "schema", "final": "final"})
    builder.add_conditional_edges("validate", validate_route, {"execute":"execute", "sqlgen": "sqlgen", "final": "final"})
    
    builder.set_entry_point("intent")

    return builder.compile(checkpointer=checkpointer)
