import requests
from typing import Any, Dict, List, TypedDict, Annotated

from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, AnyMessage
from langgraph.graph.message import add_messages
from langgraph.graph.state import END, StateGraph
from opentelemetry.trace import Status, StatusCode, get_tracer

tracer = get_tracer(__name__)

class GraphState(TypedDict):
    messages: List[AnyMessage]
    history: Annotated[list[AnyMessage], add_messages]


def make_agent_node(step_name: str, agent_url: str):
    def call_agent(base_url: str, messages: List[BaseMessage]) -> Dict[str, Any]:
        with tracer.start_as_current_span(f"call_agent_{step_name}") as span:
            span.set_attributes({"agent.url": base_url,
                                 "agent.step_name": step_name,
                                 "input.messages.count": len(messages),
                                 "agent.type": step_name})
            
            def msg2dict(m: BaseMessage) -> Dict[str, Any]:
                return {"role": "human" if isinstance(m, HumanMessage) else "ai",
                        "content": m.content,
                        "name": getattr(m, "name", None),
                        "additional_kwargs": getattr(m, "additional_kwargs", {})}
            
            span.add_event(f"Calling {step_name} agent", 
                           attributes={"url": f"{base_url}/messages",
                                       "messages.count": len(messages)})
            
            try:
                resp = requests.post(f"{base_url}/messages",
                                     json=[msg2dict(m) for m in messages],
                                     timeout=30)
                resp.raise_for_status()
                result = resp.json()
                
                span.set_attributes({"response.status": resp.status_code,
                                     "has_direct_answer": "direct_answer" in result,
                                     "result.keys": str(list(result.keys()))})
                
                span.add_event(f"{step_name} agent response received")
                span.set_status(Status(StatusCode.OK))
                
                return result
                
            except requests.exceptions.RequestException as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

    def gen_node(state: GraphState) -> GraphState:
        with tracer.start_as_current_span(f"agent_node_{step_name}") as span:
            span.set_attributes({"agent.step_name": step_name,
                                 "history.count": len(state.get('history', [])),
                                 "messages.count": len(state.get('messages', []))})
            
            span.add_event(f"Processing {step_name} node")
            
            structured_result = call_agent(agent_url, 
                                           state['history'] + state["messages"])
            
            result_message = AIMessage(content=structured_result.get("direct_answer") or "", 
                                       additional_kwargs=structured_result, 
                                       name=step_name)
            
            span.add_event(f"{step_name} node completed", 
                           attributes={"has_content": bool(result_message.content),
                                       "additional_kwargs.keys": str(list(result_message.additional_kwargs.keys()))})
            
            return {"messages": state["messages"] + [result_message]}

    return gen_node



intent_node   = make_agent_node("intent", "http://intent-agent:8001")
schema_node   = make_agent_node("schema", "http://schema-agent:8002")
sqlgen_node   = make_agent_node("sqlgen", "http://sql-gen-agent:8003")
validator_node = make_agent_node("validate", "http://sql-validator-agent:8004")
executor_node  = make_agent_node("execute", "http://sql-executor-agent:8005")


def intent_route(state: GraphState) -> str:
    with tracer.start_as_current_span("intent_routing") as span:
        last_msg = state["messages"][-1]
        route_decision = last_msg.additional_kwargs.get("route", "sql_pipeline")
        decision = "final" if route_decision == "direct_answer" else "schema"
        
        span.set_attributes({"routing.decision": decision,
                             "routing.route_value": route_decision})
        
        return decision


def validate_route(state: GraphState) -> str:
    with tracer.start_as_current_span("validate_routing") as span:
        last_msg = state["messages"][-1]
        decision = last_msg.additional_kwargs.get("decision", "pass")
        route = last_msg.additional_kwargs.get("route", "sql_pipeline")
        
        result = "execute" if decision == "pass" else "sqlgen" if route == "sql_pipeline" else "final"
        
        span.set_attributes({"routing.decision": decision,
                             "routing.route": route,
                             "routing.next_node": result})
        
        return result


def final_node(state: GraphState) -> GraphState:
    with tracer.start_as_current_span("final_node") as span:
        span.set_attributes({"messages.total": len(state["messages"]),
                             "len_history": 0 if not 'history' in state else len(state["history"])})
        
        return {'history': [state["messages"][0], state["messages"][-1]]}


def gen_graph(checkpointer):
    with tracer.start_as_current_span("build_graph") as span:
        span.set_attributes({"graph.type": "StateGraph",
                             "checkpointer.type": type(checkpointer).__name__})
        
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
        
        builder.add_conditional_edges("intent", 
                                      intent_route, 
                                      {"schema": "schema", "final": "final"})
        builder.add_conditional_edges("validate", 
                                      validate_route, 
                                      {"execute": "execute", "sqlgen": "sqlgen", "final": "final"})
        
        builder.set_entry_point("intent")
        
        span.add_event("Graph compilation completed")
        
        graph = builder.compile(checkpointer=checkpointer)
        
        graph_ainvoke = graph.ainvoke
        
        async def traced_ainvoke(input_state, config=None):
            with tracer.start_as_current_span("graph_execution") as exec_span:
                if config and "configurable" in config:
                    exec_span.set_attributes({"thread_id": config["configurable"].get("thread_id", "unknown")})
                
                exec_span.add_event("Starting graph execution")
                result = await graph_ainvoke(input_state, config)
                exec_span.add_event("Graph execution completed")
                
                return result
        
        graph.ainvoke = traced_ainvoke
        
        return graph
