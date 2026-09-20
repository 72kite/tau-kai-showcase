"""Manual smoke test for the LLM runtime wiring (tau_core.llm) against a real running Ollama.

Requires a real Ollama instance with the two models from infra/ollama/models.md pulled
(infra/ollama/pull-models.sh) and a .env with OLLAMA_HOST/OLLAMA_MODEL set (see .env.example).

Run directly for manual testing:
    python examples/chat_repl.py

By default this loads config/servers.yaml (real domain servers only, which must be pip-installed
into this environment). To smoke-test the host/CDG plumbing without any domain server installed,
point it at the test-fixture registry instead:
    TAU_SERVERS_CONFIG_PATH=config/servers.test.yaml python examples/chat_repl.py
(PowerShell: $env:TAU_SERVERS_CONFIG_PATH = "config/servers.test.yaml")
then try "echo hello" (should execute) and "shut down vm 101" (should come back pending
approval, never executed) to see the CDG enforcing itself through the LLM path.
"""

import asyncio

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.agent import TauAssistant


async def main() -> None:
    settings = TauCoreSettings()
    host = TauCoreHost.from_settings(settings)
    assistant = TauAssistant.from_settings(host, settings)

    async with host.mcp:
        await host.mcp.connect_all()
        print("Tau is listening. Ctrl-C to quit.")
        while True:
            try:
                user_text = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not user_text:
                continue
            turn = await assistant.chat(user_text)
            print(f"tau> {turn.reply}")
            if turn.pending_approval_ids:
                print(f"     (pending approval: {', '.join(turn.pending_approval_ids)})")


if __name__ == "__main__":
    asyncio.run(main())
