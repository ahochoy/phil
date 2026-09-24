from langgraph.types import interrupt
from state import PhilState

def approval_node(state: PhilState):
    is_approved = interrupt({
        "question": "Do you want to proceed with this action?",
        "details": state["plan"]
    })

    return { "plan_approved": is_approved }
