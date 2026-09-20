from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from tau_core import crypto_store
from tau_core.cdg import CoreDirectiveViolation
from tau_core.config import EnvFileSecretsProvider, SecretsProvider, TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.identity import identity_shortcut_answer
from tau_core.llm.memory_learner import PostTurnMemoryLearner
from tau_core.llm.models import build_main_model, build_ollama_model
from tau_core.llm.prompts import (
    GENERAL_SYSTEM_PROMPT,
    MAIN_SYSTEM_PROMPT,
    SUBAGENT_SYSTEM_PROMPT,
)
from tau_core.llm.router import RouterConfidenceChecker
from tau_core.mcp_client import ServerCallError, ServerNotConnectedError
from tau_core.llm.tool_need import (
    ToolNeedClassifier,
    heuristic_servers_needed,
    heuristic_tool_need,
)
from tau_core.llm.toolset import build_toolset
from tau_core.session import MemoryTreeBackend, SessionManager, SessionRegistry
from tau_core.session import store as session_store

logger = logging.getLogger(__name__)

# One sub-agent run may make at most this many model requests. Sub-agents are for bounded,
# focused tasks; a runaway loop should hit this ceiling and report back, not spin forever.
SUBAGENT_REQUEST_LIMIT = 12

# spawn_subagents (plural, fan-out) caps how many independent tasks run concurrently in one
# call. CDG/the approval queue are safe under concurrency (guard.py is stateless, the approval
# queue's dict is lock-protected - see project-tau-plan.md §10.3 item 12), so the cap here is
# purely a resource/ollama-throughput bound, not a safety one: each sub-agent is its own model
# session against the same local Ollama instance, and unbounded fan-out would just queue behind
# it rather than actually running in parallel.
MAX_PARALLEL_SUBAGENTS = 4

# The same ceiling for a MAIN turn, which had none at all until Phase 24 - sub-agents were bounded
# from the day they were written and the agent that spawns them was not.
#
# The 2026-07-28 eval made the gap concrete: a single turn issued `call_service` **eight to twelve
# times**, and one case ("Dim the lounge lamps to 30 percent") passed only after 12 model requests.
# Each of those is a full round trip plus a router confidence check, so a wedged turn burns minutes
# of a local model's time and - for a WRITE tool like call_service - repeats a real side effect on
# the house. Retrying a light switch a dozen times is not a slow success, it is twelve commands.
#
# Higher than the sub-agent limit because a main turn legitimately does more: a genuine multi-step
# request ("check every camera and report anything unusual") fans out across several tools before
# answering, where a sub-agent is by definition one focused task. `llm_turn_timeout_seconds` still
# bounds wall-clock separately; this bounds *work*, which a timeout cannot distinguish from
# slowness on CPU-bound hardware.
MAIN_TURN_REQUEST_LIMIT = 20

DEFAULT_ROUTER_MODEL = "qwen2.5:0.5b"

@dataclass(frozen=True)
class AssistantTurn:
    reply: str
    pending_approval_ids: list[str] = field(default_factory=list)
    # Memory Tree snippets ("title: content") injected into this turn's prompt - surfaced so
    # the UI can show *why* Tau knew something (recall transparency, the audit pillar applied
    # to memory). Empty when nothing matched or no memory backend is connected.
    recalled_memories: list[str] = field(default_factory=list)
    # Phase 40 "visual answer cards": a picture worth showing alongside the reply text, e.g. from
    # research-mcp-server.search_images - {url, title, source_url, source}. None on almost every
    # turn; see tau_core.llm.toolset.IMAGE_RESULT_TOOLS for which tool calls can populate this.
    image: dict[str, str] | None = None


class TauAssistant:
    """The LLM runtime wiring in front of `TauCoreHost`: turns a user message into zero or more
    `TauCoreHost.call_tool` calls (via a per-turn PydanticAI toolset) and a natural-language reply.

    This is the piece project-tau-plan.md's Phase 1 exit criteria flagged as not yet built. It
    holds no privileged path of its own to any MCP server - every tool it exposes to the model is a
    wrapper around `host.call_tool`, so the CDG/approval/audit pipeline applies exactly as it does
    to any other caller.
    """

    def __init__(
        self,
        host: TauCoreHost,
        main_model: Model,
        router_model: Model,
        session: SessionManager | None = None,
        subagent_model: Model | None = None,
        reply_language: str = "English",
    ):
        self.host = host
        # The default/anonymous session: used for turns that carry no device key (the REPL, the
        # eval harness, tests, and any client that sends no X-Tau-Device-Id). Per-device turns get
        # their own session from `self._sessions` instead - see chat().
        self.session = session or host.session
        # Phase 12: one SessionManager per identified device, so device B's conversation never
        # enters the model's context on device A's turn (§10.1 #3 / Tier 1 #8). Each per-device
        # session is built to match host.session's wiring - the same rolling-window size and a
        # Memory Tree recall backend - so a device gets identical behaviour, just an isolated
        # history. Recall itself stays global on purpose (the Memory Tree ignores session_id;
        # per-speaker memory scoping is a separate, larger memory-mcp-server change tracked in
        # §10.1 #3's "drafts aren't bound to the speaker" note).
        # Durable by default (Phase 10.3 #11, session persistence): one shared file for every
        # device's history (tau_core.session.store.save_session_map), loaded once here and
        # rewritten in full on every device session's change - each device is a handful at a
        # time in a home, so a whole-file rewrite per turn is cheap, the same tradeoff the
        # approval queue already makes. NOTE: a session evicted by SessionRegistry's LRU cap
        # (session_max_devices) is not actively purged from this file, and if that device is
        # later re-seen it starts fresh rather than reloading its pre-eviction history - eviction
        # only happens past 64 concurrently-tracked devices, far past any real household, so this
        # is an accepted, documented gap rather than something worth the extra bookkeeping.
        device_session_store_path = host.settings.device_session_store_path
        device_session_key = (
            crypto_store.resolve_key(device_session_store_path)
            if device_session_store_path is not None
            else None
        )
        loaded_device_sessions = session_store.load_session_map(
            device_session_store_path, device_session_key
        )

        def _persist_all_device_sessions(_mgr: SessionManager) -> None:
            if device_session_store_path is None:
                return
            snapshot = {
                key: session.history()
                for key in self._sessions.keys()
                if (session := self._sessions.get(key)) is not None
            }
            session_store.save_session_map(device_session_store_path, device_session_key, snapshot)

        self._sessions = SessionRegistry(
            factory=lambda key: SessionManager(
                session_id=f"device:{key}",
                max_messages=host.settings.session_max_messages,
                memory_backend=MemoryTreeBackend(host),
                initial_messages=loaded_device_sessions.get(key),
                on_change=_persist_all_device_sessions,
            ),
            max_sessions=host.settings.session_max_devices,
        )
        # The reply language is baked into the system prompts here (default English), so it's a
        # hard default the model is told to hold regardless of tool-output language - the fix for
        # small models drifting into Thai/Chinese. Change via TAU_REPLY_LANGUAGE.
        self.reply_language = reply_language
        self._main_prompt = MAIN_SYSTEM_PROMPT.replace("__REPLY_LANGUAGE__", reply_language)
        self._general_prompt = GENERAL_SYSTEM_PROMPT.replace("__REPLY_LANGUAGE__", reply_language)
        self._subagent_prompt = SUBAGENT_SYSTEM_PROMPT.replace("__REPLY_LANGUAGE__", reply_language)
        self.agent = Agent(main_model, system_prompt=self._main_prompt)
        # Phase 18: the tool-free fast path runs the same model with the lean general prompt and no
        # toolset; the classifier is the ambiguous-case half of the tool-need gate (the heuristic is
        # the free first pass). Both reuse the model instances the full path already holds.
        self.general_agent = Agent(main_model, system_prompt=self._general_prompt)
        self.tool_need = ToolNeedClassifier(router_model)
        self.checker = RouterConfidenceChecker(router_model)
        self.memory_learner = PostTurnMemoryLearner(router_model)
        # Sub-agents default to the same model as the main agent; the override exists so tests
        # can drive the parent and the sub-agent with two different FunctionModels.
        self.subagent_model = subagent_model or main_model

    @classmethod
    def from_settings(
        cls,
        host: TauCoreHost,
        settings: TauCoreSettings | None = None,
        secrets: SecretsProvider | None = None,
    ) -> "TauAssistant":
        settings = settings or TauCoreSettings()
        secrets = secrets or EnvFileSecretsProvider()
        timeout = settings.llm_request_timeout_seconds
        main_model = build_main_model(settings, secrets)
        router_model = build_ollama_model(
            secrets.require("OLLAMA_HOST"),
            secrets.get("OLLAMA_ROUTER_MODEL") or DEFAULT_ROUTER_MODEL,
            timeout=timeout,
        )

        return cls(host, main_model, router_model, reply_language=settings.reply_language)

    async def chat(
        self, user_text: str, session_key: str | None = None, speaker: str | None = None
    ) -> AssistantTurn:
        """Run one chat turn. `session_key` (the calling device's id) selects an isolated
        per-device conversation history; None/empty falls back to the shared default session,
        which is what the REPL, the eval harness, and any client that sends no device id use.

        `speaker` is the voice-identified person for this turn (Phase 13.5). It scopes memory:
        drafts Tau writes are bound to this speaker, and recall returns only this speaker's
        memories plus shared ones - stamped server-side here and in the toolset, never taken from
        the model. None/empty = unknown speaker (shared memories only)."""
        session = self._resolve_session(session_key)
        owner = (speaker or "").strip().lower()
        session.add_message("user", user_text)

        # Identity/creator questions get a fixed answer, before any model or tool runs (Phase 16):
        # the model+router could not be trusted with them (it web-searched "who created you"). Gated
        # to English so a configured non-English reply language isn't violated by a canned answer.
        if self.reply_language.strip().lower().startswith("english"):
            identity_reply = identity_shortcut_answer(user_text)
            if identity_reply is not None:
                session.add_message("assistant", identity_reply)
                return AssistantTurn(reply=identity_reply)

        # Phase 2.8 default recall: surfaces Memory Tree context at the start of every turn, scoped
        # to this speaker (Phase 13.5). Runs on BOTH paths - it's automatic context injection, not a
        # model tool call, so a "what's my sister's name" turn is still memory-backed even when it
        # takes the tool-free fast path below. Empty (and free) unless a memory backend is wired.
        recalled = await session.recall(user_text, owner=owner)

        # Both accumulators are declared before the tool-need gate so every path below can hand
        # them to _finish_turn. The tool-free path leaves them empty, which is exactly what it
        # produced before (AssistantTurn's default_factory=list, and image=None).
        pending_approval_ids: list[str] = []
        image_results: list[dict[str, str]] = []

        # Phase 18 tool-need gate: does this turn need the toolset at all? A general-knowledge or
        # conversational turn takes a lean, tool-free path - no ~77 tool schemas, so no spurious
        # tool calls and a tighter answer. Biased hard toward the full path (see tool_need's safety
        # contract); a false "needs tools" only costs latency, a false "tool-free" would hallucinate.
        if not await self._needs_tools(user_text):
            prompt = self._render_prompt(session, user_text, recalled)
            result = await self.general_agent.run(prompt)
            return self._finish_turn(
                session, user_text, result.output, owner,
                recalled, pending_approval_ids, image_results,
            )

        # Phase 23: optionally narrow the roster to the servers this turn is about. `None` means
        # "could not tell" and build_toolset's own default (every registered server) applies -
        # see heuristic_servers_needed for why every uncertain case resolves that way.
        scoped_servers = self._scope_servers(user_text)
        toolset = await build_toolset(
            self.host, self.checker, user_text, pending_approval_ids,
            servers=scoped_servers, speaker=owner, image_results=image_results,
        )
        self._add_spawn_subagent_tool(toolset, pending_approval_ids, owner, image_results)

        prompt = self._render_prompt(session, user_text, recalled)
        try:
            result = await self.agent.run(
                prompt,
                toolsets=[toolset],
                usage_limits=UsageLimits(request_limit=MAIN_TURN_REQUEST_LIMIT),
            )
        except UsageLimitExceeded as exc:
            # A turn that hit the ceiling has usually been re-issuing one tool call in a loop
            # (see MAIN_TURN_REQUEST_LIMIT). Tell the user plainly rather than raising: the tool
            # calls it DID make already happened, so a 502 would hide real side effects behind an
            # error, and any approval it queued is still pending and still valid.
            logger.warning(
                "Chat turn hit the %d-request ceiling: %s", MAIN_TURN_REQUEST_LIMIT, exc
            )
            reply = (
                "I got stuck repeating myself on that one and stopped before it ran away. "
                "Some of it may have gone through - worth checking before asking again."
            )
            return self._finish_turn(
                session, user_text, reply, owner,
                recalled, pending_approval_ids, image_results, learn=False,
            )
        except UnexpectedModelBehavior as exc:
            # Found live 2026-09-08 (Phase 36 model sweep): a small/mid model that never produces
            # a validly-structured final output after pydantic-ai's retries raises this - distinct
            # from UsageLimitExceeded above (that's a request-count ceiling; this is "every attempt
            # came back malformed"). Previously uncaught here, so it propagated out of chat() -
            # harmless in the eval harness (its own per-case try/except degrades it to one ERROR
            # verdict) but would have been a raw 502 out of /api/chat on a live turn, on exactly
            # the small-model failure mode this whole phase is measuring. Same reasoning as above:
            # don't raise - any tool calls the turn already made before the final output failed
            # are real and their approvals are still valid.
            logger.warning("Chat turn's final output never validated: %s", exc)
            reply = (
                "I couldn't put together a clear answer to that one - what I had didn't look "
                "right, so I'm not guessing. Some of it may have gone through - worth checking "
                "before asking again."
            )
            return self._finish_turn(
                session, user_text, reply, owner,
                recalled, pending_approval_ids, image_results, learn=False,
            )

        return self._finish_turn(
            session, user_text, result.output, owner,
            recalled, pending_approval_ids, image_results,
        )

    def _finish_turn(
        self,
        session: SessionManager,
        user_text: str,
        reply: str,
        owner: str,
        recalled: list[str],
        pending_approval_ids: list[str],
        image_results: list[dict[str, str]],
        *,
        learn: bool = True,
    ) -> AssistantTurn:
        """The shared tail of every chat() path that produced a reply: record it in the session,
        optionally feed the post-turn memory learner, and pack the turn's side-channel outputs into
        an AssistantTurn.

        There were four copies of this before Phase 35, and Phase 34 had just made that concretely
        worse by requiring the new _learn_from_turn call to be added to two of them.

        `learn=False` is load-bearing, not an ergonomic default. The UsageLimitExceeded and
        UnexpectedModelBehavior paths return a canned apology Tau composed about its OWN
        malfunction; drafting a memory from "I got stuck repeating myself on that one" would write
        the assistant's failure mode into the household's long-term memory as though it were
        something learned about the user. Those two paths always skipped the learner - this flag is
        what keeps them skipping it now that the tail is shared.
        """
        session.add_message("assistant", reply)
        if learn:
            self._learn_from_turn(user_text, reply, owner)
        return AssistantTurn(
            reply=reply,
            pending_approval_ids=pending_approval_ids,
            recalled_memories=recalled,
            image=image_results[-1] if image_results else None,
        )

    def _learn_from_turn(self, user_text: str, reply: str, speaker: str) -> None:
        """Fires the post-turn memory classifier as a background task, after the reply this
        method's caller is about to return - so it can never add to a turn's perceived latency.
        Fire-and-forget by design: a dropped or slow draft is not something the user is waiting
        on, unlike every other host.call_tool caller in this file.
        """
        if not self.host.settings.background_memory_learning_enabled:
            return
        asyncio.create_task(self._draft_from_turn(user_text, reply, speaker))

    async def _draft_from_turn(self, user_text: str, reply: str, speaker: str) -> None:
        candidate = await self.memory_learner.extract(user_text, reply)
        if candidate is None or not candidate.should_draft or not candidate.content.strip():
            return
        try:
            await self.host.call_tool(
                "memory-mcp-server",
                "draft_memory",
                {"title": candidate.title or "Observation", "content": candidate.content, "owner": speaker},
                # No opinion to offer - this bypasses the router by construction, not through the
                # CDG-tier bypass in toolset.py (there's no model call here to score). draft_memory
                # is CDG Effect.ALLOW, so this executes and lands as a reviewable draft, same as
                # every model-issued call to it.
                confidence=None,
                requested_by=f"post-turn-learner:{speaker or 'unattributed'}",
            )
        except (ServerCallError, ServerNotConnectedError) as exc:
            # memory-mcp-server being down just means this turn's inference is lost, not that
            # anything is broken - same best-effort posture as the scheduler's own skip-if-
            # disconnected jobs.
            logger.info("Post-turn memory learner could not reach memory-mcp-server: %s", exc)
        except CoreDirectiveViolation:
            # Should not happen (draft_memory is Effect.ALLOW), but a ruleset edit must never
            # crash a background task over a tool call nobody is waiting on.
            logger.warning("Post-turn memory learner's draft_memory call was denied by the CDG")

    def _scope_servers(self, user_text: str) -> list[str] | None:
        """The servers this turn may use, or None for "all of them" (the pre-Phase-23 behaviour).

        Returns None whenever the feature is off, so with TAU_SCOPE_SERVERS_PER_TURN unset this
        method is the only thing Phase 23 adds to the hot path - one boolean check.
        """
        if not self.host.settings.scope_servers_per_turn:
            return None
        registered = self.host.mcp.registered_servers()
        scope = heuristic_servers_needed(user_text, registered)
        if scope is None:
            return None
        logger.info(
            "Scoped turn to %d/%d servers: %s", len(scope), len(registered), sorted(scope)
        )
        return sorted(scope)

    async def _needs_tools(self, user_text: str) -> bool:
        """The hybrid gate: the instant heuristic first, and only its "uncertain" middle pays for a
        classifier call. Returns True (keep the full toolset path) on anything but a confident
        tool-free verdict - see tau_core.llm.tool_need for the safety contract."""
        verdict = heuristic_tool_need(user_text)
        if verdict == "tools":
            return True
        if verdict == "no_tools":
            return False
        return await self.tool_need.needs_tools(user_text)

    async def _run_subagent_task(
        self,
        task: str,
        servers: list[str],
        pending_approval_ids: list[str],
        speaker: str,
        image_results: list[dict[str, str]] | None = None,
    ) -> str:
        """One sub-agent run: scope a toolset to `servers`, execute `task` against it, and
        return its report as plain text. Shared by both spawn_subagent (one task) and
        spawn_subagents (many, concurrently) so they can never drift apart on scoping/limits.
        """
        try:
            sub_toolset = await build_toolset(
                self.host, self.checker, task, pending_approval_ids, servers=servers,
                speaker=speaker, allow_subagent_only_servers=True, image_results=image_results,
            )
        except ValueError as exc:
            return f"SUBAGENT_NOT_STARTED: {exc}"

        logger.info("spawn_subagent: task=%r servers=%r", task, servers)
        sub_agent = Agent(self.subagent_model, system_prompt=self._subagent_prompt)
        try:
            result = await sub_agent.run(
                task,
                toolsets=[sub_toolset],
                usage_limits=UsageLimits(request_limit=SUBAGENT_REQUEST_LIMIT),
            )
        except Exception as exc:  # noqa: BLE001 - report failure to the model, don't kill the turn
            logger.warning("spawn_subagent failed: task=%r", task, exc_info=True)
            return f"SUBAGENT_FAILED: {type(exc).__name__}: {exc}"
        return result.output

    def _add_spawn_subagent_tool(
        self,
        toolset,
        pending_approval_ids: list[str],
        speaker: str = "",
        image_results: list[dict[str, str]] | None = None,
    ) -> None:
        """Adds spawn_subagent and spawn_subagents to the main agent's toolset (and only the
        main agent's - the sub-agent's own toolset comes from build_toolset, which never
        includes either, so sub-agents cannot recurse). A sub-agent holds no privileged path to
        anything: every MCP tool it sees is the same host.call_tool wrapper the main agent gets,
        so the CDG, approval queue, and audit log apply to its calls identically, and any
        approval it triggers lands in the same turn's pending_approval_ids.
        """

        async def spawn_subagent(task: str, servers: list[str]) -> str:
            """Delegate one focused multi-step task to a fresh sub-agent and return its report.

            Args:
                task: Complete, self-contained task description. The sub-agent sees nothing of
                    the conversation - include every detail it needs.
                servers: MCP server names the sub-agent may use, e.g. ["vision-mcp-server"].
                    Scope this as narrowly as the task allows.
            """
            return await self._run_subagent_task(
                task, servers, pending_approval_ids, speaker, image_results
            )

        async def spawn_subagents(tasks: list[dict]) -> list[str]:
            """Delegate several INDEPENDENT tasks to fresh sub-agents that run concurrently, and
            return their reports in the same order as `tasks`. Only use this when the tasks do
            not depend on each other's results (e.g. "check every camera" as N independent
            per-camera checks) - if one task needs another's output, use spawn_subagent
            sequentially instead. Capped at a small number running at once; extra tasks queue and
            run as slots free up, they are not dropped.

            Args:
                tasks: Each item is {"task": "...", "servers": [...]} - same fields as
                    spawn_subagent's own arguments, one dict per independent task.
            """
            semaphore = asyncio.Semaphore(MAX_PARALLEL_SUBAGENTS)

            async def run_bounded(item: dict) -> str:
                async with semaphore:
                    return await self._run_subagent_task(
                        str(item.get("task", "")),
                        list(item.get("servers", [])),
                        pending_approval_ids,
                        speaker,
                        image_results,
                    )

            return list(await asyncio.gather(*(run_bounded(item) for item in tasks)))

        toolset.add_function(spawn_subagent, name="spawn_subagent")
        toolset.add_function(spawn_subagents, name="spawn_subagents")

    def _resolve_session(self, session_key: str | None) -> SessionManager:
        """The default session for an anonymous turn, or this device's own isolated session.
        A falsy key (no X-Tau-Device-Id) shares the default, since there is nothing to isolate
        it by - anonymous callers cannot be told apart, so they cannot be separated."""
        key = (session_key or "").strip()
        if not key:
            return self.session
        return self._sessions.get_or_create(key)

    def _render_prompt(
        self, session: SessionManager, user_text: str, recalled: list[str] | None = None
    ) -> str:
        sections: list[str] = []
        if recalled:
            memories = "\n".join(f"- {memory}" for memory in recalled)
            sections.append(
                "Possibly relevant long-term memories (from the Memory Tree; ignore any that "
                f"don't apply to this message):\n{memories}"
            )
        history = session.history()[:-1]  # exclude the user message just added
        if history:
            transcript = "\n".join(f"{message.role}: {message.content}" for message in history)
            sections.append(f"Recent conversation:\n{transcript}")
        if not sections:
            return user_text
        return "\n\n".join(sections) + f"\n\nuser: {user_text}"
