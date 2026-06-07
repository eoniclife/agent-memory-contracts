"""LangChain integration example.

Demonstrates how to use ``ContractsMemory`` as a drop-in
``memory=`` argument for any LangChain chain that uses
``BaseMemory``.

This example does not require an LLM API key. It uses a
fake LLM that returns canned responses, so the full chain
loop (load_memory_variables -> fake LLM -> save_context)
runs end-to-end.

To use a real LLM, swap ``FakeListLLM`` for
``langchain_openai.ChatOpenAI`` (or any other chat model)
and set the appropriate API key.
"""

from __future__ import annotations

from langchain_classic.base_memory import BaseMemory
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.prompts import ChatPromptTemplate

from agent_memory_contracts.integrations.langchain import (
    ContractsMemory,
)


def main() -> None:
    # 1. Create a memory for the session.
    memory: BaseMemory = ContractsMemory(
        session_id="example-session",
    )

    # 2. Use any LangChain chat model. The fake model
    #    returns canned responses so no API key is needed.
    llm = FakeListChatModel(
        responses=[
            "Paris.",
            "Madrid.",
            "I remember we talked about European capitals earlier.",
        ]
    )

    # 3. Build a prompt that uses the context_pack memory
    #    variable. The memory's load_memory_variables()
    #    returns {"context_pack": {...}}; the prompt
    #    references that variable.
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a helpful assistant. You have access to "
                "the following context from this conversation:\n\n"
                "{context_pack}\n\n"
                "Use it to answer the user's question.",
            ),
            ("human", "{input}"),
        ]
    )

    # 4. Manually run the chain. (A real LangChain chain
    #    would handle this loop, but we want to make the
    #    memory calls explicit.)
    questions = [
        "What is the capital of France?",
        "And Spain?",
        "What did we talk about earlier?",
    ]

    for i, question in enumerate(questions):
        # Read memory
        mem_vars = memory.load_memory_variables({"input": question})
        # Format prompt
        msgs = prompt.format_messages(
            input=question,
            context_pack=_format_context_pack(mem_vars["context_pack"]),
        )
        # Call LLM
        response = llm.invoke(msgs)
        response_text = (
            response.content
            if hasattr(response, "content")
            else str(response)
        )
        print(f"Q: {question}")
        print(f"A: {response_text}")
        # Write memory
        memory.save_context({"input": question}, {"response": response_text})

    # 5. Show the session state.
    final = memory.load_memory_variables({"input": "summary"})
    cp = final["context_pack"]
    print()
    print("=" * 70)
    print("Final session state:")
    print(f"  context_pack_id: {cp['context_pack_id']}")
    print(f"  records (turns): {len(cp['records'])}")
    print(f"  evidence (spans): {len(cp['evidence'])}")
    print(f"  sources: {len(cp['sources'])}")
    print("=" * 70)


def _format_context_pack(cp: dict) -> str:
    """Format a context_pack dict for the prompt template."""
    if cp.get("context_pack_id") is None:
        return "(no prior context)"
    lines = []
    for record in cp.get("records", []):
        summary = record.get("summary", "")
        lines.append(f"- Turn: {summary}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
