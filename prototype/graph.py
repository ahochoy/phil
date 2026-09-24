from langgraph.graph import StateGraph
from langgraph.types import Command, interrupt
from langgraph.checkpoint.memory import InMemorySaver
from typing import TypedDict, List, Optional
from rich import table
from rich.table import Table
from rich.console import Console

console = Console()

class ArchitectsResponse(TypedDict):
    breakdown: List[TaskItem]
    description: str

class TaskItem(TypedDict):
    id: str
    description: str
    status: str

class PhilState(TypedDict):
    objective: str
    directory: str
    plan: ArchitectsResponse
    plan_approved: Optional[bool]
    plan_feedback: Optional[str]
    current_task_id: Optional[str]
    latest_error: Optional[str]

def print_plan(plan: ArchitectsResponse):
    table = Table(title=plan.get("description", "Plan"))

    table.add_column("ID", justify="right", style="cyan", no_wrap=True)
    table.add_column("Description", style="magenta")
    table.add_column("Status", justify="right", style="green")

    for task in plan["breakdown"]:
        table.add_row(task["id"], task["description"], task["status"])
    
    console.print(table)

def architect(state: PhilState):
    # Will take objective and directory, and produce a plan
    # It will require HITL for the initial plan - any future requests from the tester or the deployer will not require HITL
    console.print("Running architect node with objective", style="blue")

    return({
        "plan": {
            "breakdown": [
                {"id": "task1", "description": "Design the map section UI", "status": "pending"},
                {"id": "task2", "description": "Implement the map section UI", "status": "pending"},
                {"id": "task3", "description": "Write tests for the map section", "status": "pending"},
                {"id": "task4", "description": "Deploy the map section", "status": "pending"},
            ],
            "description": "The plan is to first design the UI for the map section, then implement the UI, write tests for it, and finally deploy it."
        }
    })

def developer(state: PhilState):
    # Will take the plan (list of tasks) and iterate over them in a TDD fashion, producing code and tests for each task
    console.print("Running developer node with plan", style="green")

    plan = state["plan"]
    updated_breakdown = list(map(lambda task: {**task, "status": "completed"}, plan["breakdown"]))
    plan |= {"breakdown": updated_breakdown}

    return({"plan": plan})


def tester(state: PhilState):
    # Will provide domain expert feedback and ensure the code produced by the developer node is correct and meets the requirements of the plan
    # It will raise any non-functional concerns and return feedback to the architect and developer nodes for iteration
    # If there are concerns it will set the plan_approved field to False and provide feedback in the plan_feedback field, which will be used by the architect and developer nodes to iterate on the plan and code until it is approved
    print("Running tester node with plan")

    return(state)


def deployer(state: PhilState):
    # It will take the completed code and raise a PR, preparing a thoughtful description of the changes and the reasoning behind them, and then merge the PR once it is approved by a human reviewer
    # It will send a notification that the PR is stable and ready for review
    # If the PR is rejected, it will set the plan_approved field to False and provide feedback in the plan_feedback field, which will be used by the architect and developer nodes to iterate on the plan and code until it is approved
    print("Running deployer node with plan:", state["plan"])

def check_plan(state: PhilState):
    # Check if approval is needed - if there is no feedback and no approval, then we need to request approval. If there is feedback or approval, then we can proceed to the next step
    print("Checking approvals and feedback for the plan")

    is_approved = interrupt({ "question": "Do you want to proceed with this action?" })

    return Command(goto="developer" if is_approved else "architect")
    
    
def verify_code(state: PhilState):
    # Check if the code produced by the developer node meets the requirements of the plan and is correct. If it is not, then we need to request feedback from the tester node. If it is, then we can proceed to the next step
    code_is_correct = True # Placeholder for actual code verification logic

    if code_is_correct:
        return "deployer"
    else:
        return "architect"

# The Graph Construction
workflow = StateGraph(PhilState)

workflow.add_node("architect", architect)
workflow.add_node("developer", developer)
workflow.add_node("check_plan", check_plan)
# workflow.add_node("tester", tester)
# workflow.add_node("deployer", deployer)

workflow.set_entry_point("architect")
workflow.add_edge("architect", "check_plan")
workflow.set_finish_point("developer")

phil = workflow.compile(checkpointer=InMemorySaver())
config = {"configurable": {"thread_id": "approval-123"}}

initial = phil.invoke(
    {
        "objective": "Create a map section that displays listings on a map",
        "directory": "/Users/hochoy/code/local-vibe-exchange"
    },
    config=config,
)

print(initial["__interrupt__"])  

# Resume with the decision; True routes to proceed, False to cancel
resumed = phil.invoke(Command(resume=False), config=config)
print(initial["__interrupt__"])

resumed = phil.invoke(Command(resume=True), config=config)

print_plan(resumed.get("plan"))
