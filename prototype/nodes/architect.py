from langchain_openrouter import ChatOpenRouter
from dotenv import load_dotenv
from state import PhilState, ArchitectsResponse
from prompts.loader import architect_prompt
from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
import getpass
import os

load_dotenv()  # Load environment variables from .env file

if not os.getenv("OPENROUTER_API_KEY"):
    os.environ["OPENROUTER_API_KEY"] = getpass.getpass("Enter your OpenRouter API key: ")

def architect(state: PhilState):
    agent = create_deep_agent(
        model="openrouter:poolside/laguna-m.1:free",
        system_prompt=architect_prompt,
        backend=FilesystemBackend(root_dir=state["directory"], virtual_mode=True)
    )

    response = agent.invoke({
        "messages": [{"role": "user", "content": state["objective"]}]
    })

    return {
        "plan": response
    }
