import uuid

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from phil.agents.collector import UsageCollector


def generation(*, content="hi", input_tokens=10, output_tokens=5, cost=None, model_name=None) -> ChatGeneration:
    response_metadata = {}
    if cost is not None:
        response_metadata["cost"] = cost
    if model_name is not None:
        response_metadata["model_name"] = model_name
    message = AIMessage(
        content=content,
        usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens},
        response_metadata=response_metadata,
    )
    return ChatGeneration(message=message)


def test_on_llm_end_records_top_level_and_nested_calls():
    collector = UsageCollector()
    collector.on_llm_end(
        LLMResult(generations=[[generation(input_tokens=10, output_tokens=5, cost=0.01, model_name="openai/gpt-6-sol")]]),
        run_id=uuid.uuid4(),
    )
    collector.on_llm_end(
        LLMResult(generations=[[generation(input_tokens=20, output_tokens=8, cost=0.02, model_name="openai/gpt-6-sol")]]),
        run_id=uuid.uuid4(),
    )
    collector.on_llm_end(
        LLMResult(generations=[[generation(input_tokens=30, output_tokens=12, cost=0.03, model_name="openai/gpt-6-sol")]]),
        run_id=uuid.uuid4(),
        parent_run_id=uuid.uuid4(),
    )
    assert len(collector.calls) == 3
    assert [call.reported_cost for call in collector.calls] == [0.01, 0.02, 0.03]
    assert all(call.model == "openai/gpt-6-sol" for call in collector.calls)
    assert [(call.input_tokens, call.output_tokens) for call in collector.calls] == [(10, 5), (20, 8), (30, 12)]


def test_on_llm_end_without_cost_leaves_reported_cost_none():
    collector = UsageCollector()
    collector.on_llm_end(
        LLMResult(generations=[[generation(input_tokens=10, output_tokens=5)]]),
        run_id=uuid.uuid4(),
    )
    [call] = collector.calls
    assert call.reported_cost is None
    assert call.model is None


def test_on_llm_end_falls_back_to_token_usage_in_llm_output():
    collector = UsageCollector()
    message = AIMessage(content="hi")  # no usage_metadata on the message itself
    result = LLMResult(
        generations=[[ChatGeneration(message=message)]],
        llm_output={"token_usage": {"input_tokens": 7, "output_tokens": 3}},
    )
    collector.on_llm_end(result, run_id=uuid.uuid4())
    [call] = collector.calls
    assert (call.input_tokens, call.output_tokens) == (7, 3)


def test_token_usage_fallback_reads_openai_style_prompt_and_completion_tokens():
    collector = UsageCollector()
    result = LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content="hi"))]],
        llm_output={"token_usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}},
    )
    collector.on_llm_end(result, run_id=uuid.uuid4())
    [call] = collector.calls
    assert (call.input_tokens, call.output_tokens) == (11, 4)


def test_token_usage_fallback_is_applied_once_across_several_generations():
    # llm_output's token_usage is the aggregate for the whole response: with n > 1 generations
    # it must be counted once, not once per generation.
    collector = UsageCollector()
    result = LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content="a")), ChatGeneration(message=AIMessage(content="b"))]],
        llm_output={"token_usage": {"input_tokens": 7, "output_tokens": 3}},
    )
    collector.on_llm_end(result, run_id=uuid.uuid4())
    assert sum(c.input_tokens for c in collector.calls) == 7
    assert sum(c.output_tokens for c in collector.calls) == 3


def test_on_tool_start_counts_tools_and_ignores_the_contract_tool():
    collector = UsageCollector(ignore_tools={"PlanCritique"})
    collector.on_tool_start({"name": "read_file"}, "", run_id=uuid.uuid4())
    collector.on_tool_start({"name": "read_file"}, "", run_id=uuid.uuid4())
    collector.on_tool_start({"name": "run_shell"}, "", run_id=uuid.uuid4())
    collector.on_tool_start({"name": "PlanCritique"}, "", run_id=uuid.uuid4())
    assert collector.tool_calls == {"read_file": 2, "run_shell": 1}


def test_on_tool_start_reads_name_from_kwargs_when_serialized_lacks_it():
    collector = UsageCollector()
    collector.on_tool_start({}, "", run_id=uuid.uuid4(), name="read_file")
    assert collector.tool_calls == {"read_file": 1}


def test_on_tool_start_records_file_tool_paths_from_input_str_and_inputs():
    collector = UsageCollector()
    collector.on_tool_start({"name": "read_file"}, '{"file_path": "/src/app.py", "offset": 0}', run_id=uuid.uuid4())
    collector.on_tool_start({"name": "ls"}, "{'path': '/src'}", run_id=uuid.uuid4())
    collector.on_tool_start({"name": "grep"}, "not a literal", run_id=uuid.uuid4(), inputs={"pattern": "x", "path": "/lib"})
    collector.on_tool_start({"name": "glob"}, '{"pattern": "**/*.py"}', run_id=uuid.uuid4())
    collector.on_tool_start({"name": "run_shell"}, '{"command": "ls", "path": "/nope"}', run_id=uuid.uuid4())
    collector.on_tool_start({"name": "read_file"}, "garbage", run_id=uuid.uuid4())
    collector.on_tool_start({"name": "read_file"}, '["/a"]', run_id=uuid.uuid4())
    collector.on_tool_start({"name": "read_file"}, '{"file_path": 3}', run_id=uuid.uuid4())
    assert collector.tool_paths == {"read_file": ["/src/app.py"], "ls": ["/src"], "grep": ["/lib"]}


def test_tool_paths_are_deduplicated_and_capped_per_tool():
    collector = UsageCollector()
    for index in range(60):
        collector.on_tool_start({"name": "read_file"}, f'{{"file_path": "/f{index}"}}', run_id=uuid.uuid4())
        collector.on_tool_start({"name": "read_file"}, '{"file_path": "/f0"}', run_id=uuid.uuid4())
    assert collector.tool_paths["read_file"] == [f"/f{index}" for index in range(50)]


def test_tool_paths_can_record_into_a_shared_sink():
    sink: dict[str, list[str]] = {}
    UsageCollector(tool_paths=sink).on_tool_start({"name": "ls"}, '{"path": "/a"}', run_id=uuid.uuid4())
    UsageCollector(tool_paths=sink).on_tool_start({"name": "ls"}, '{"path": "/b"}', run_id=uuid.uuid4())
    assert sink == {"ls": ["/a", "/b"]}


def test_end_to_end_propagation_through_create_agent():
    model = GenericFakeChatModel(
        messages=iter(
            [AIMessage(content="hi", usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7})]
        )
    )
    agent = create_agent(model, tools=[])
    collector = UsageCollector()
    agent.invoke({"messages": [{"role": "user", "content": "hello"}]}, config={"callbacks": [collector]})
    assert len(collector.calls) == 1
    assert (collector.calls[0].input_tokens, collector.calls[0].output_tokens) == (5, 2)
