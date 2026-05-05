import json
import ollama

from config import OLLAMA_MODEL, OLLAMA_HOST, SYSTEM_PROMPT, MAX_TOOL_ITERATIONS
from logger import log, ollama_chat
from tool_schemas import TOOLS
from k8s_tools import list_resources, delete_resource, apply_manifest

_TOOL_FN_MAP = {
    "list_resources": list_resources,
    "delete_resource": delete_resource,
    "apply_manifest": apply_manifest,
}

_SEP = "─" * 60


def _execute_tool(name: str, args: dict) -> str:
    fn = _TOOL_FN_MAP.get(name)
    if not fn:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        return fn(**args)
    except TypeError as e:
        return json.dumps({"error": f"Invalid arguments for {name}: {e}"})


def _build_client() -> ollama.Client:
    return ollama.Client(host=OLLAMA_HOST)


def chat(user_message: str) -> str:
    cl = _build_client()
    history = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    log("USER", user_message)

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = ollama_chat(
            cl,
            label=f"AGENT call #{iteration + 1}",
            model=OLLAMA_MODEL,
            options={"temperature": 0},
            messages=history,
            tools=TOOLS,
            stream=False,
        )
        msg = response.message
        content = msg.content or ""
        tool_calls = msg.tool_calls

        log("ASSISTANT", content)

        if not tool_calls:
            log("AGENT", "done")
            return content

        history.append({
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            name = tc.function.name
            args = tc.function.arguments if isinstance(tc.function.arguments, dict) else {}
            log("TOOL", f"{name}  args={json.dumps(args)}")
            result = _execute_tool(name, args)
            preview = result if len(result) <= 300 else result[:300] + "  ..."
            log("RESULT", preview)
            history.append({"role": "tool", "content": result})

    log("AGENT", f"WARNING: max iterations ({MAX_TOOL_ITERATIONS}) reached")
    fallback = ollama_chat(
        cl,
        label="AGENT fallback",
        model=OLLAMA_MODEL,
        messages=history,
        stream=False,
    )
    content = fallback.message.content or ""
    log("ASSISTANT", content)
    return content
