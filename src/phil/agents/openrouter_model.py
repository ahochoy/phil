"""Phil's OpenRouter chat model: ChatOpenRouter, with files in tool results sent in OpenRouter's form.

deepagents' `read_file` returns an image, PDF, audio or video file as a LangChain data block (for
an image, `{"type": "image", "base64": …, "mime_type": …}`). ChatOpenRouter converts such blocks
in user messages but sends a tool result's content as it is, and the OpenRouter SDK then refuses
the whole request before sending it (a pydantic ValidationError on `body.N.tool.content`).
OpenRouter accepts these parts in tool results too, so tool results go through the same
conversion as user messages: langchain_openrouter's own `_format_message_content`. It's private,
so tests/agents/test_providers.py pins what it does; drop this class once ChatOpenRouter converts
tool results itself.

Imported only when an OpenRouter model is built: it loads the LangChain and OpenRouter stack.
"""

from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage
from langchain_openrouter import ChatOpenRouter
from langchain_openrouter.chat_models import _format_message_content


class PhilChatOpenRouter(ChatOpenRouter):
    def _create_message_dicts(
        self, messages: list[BaseMessage], stop: list[str] | None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        message_dicts, params = super()._create_message_dicts(messages, stop)
        for message, message_dict in zip(messages, message_dicts, strict=True):
            if isinstance(message, ToolMessage):
                message_dict["content"] = _format_message_content(message_dict["content"])
        return message_dicts, params
