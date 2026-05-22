# Combined Holmes server: standard /api/chat + /healthz + /readyz
# PLUS experimental /api/agui/chat (AG-UI protocol for Headlamp ai-assistant plugin).
#
# Mounted over /app/server.py by the install-holmes Ansible role so the Helm
# chart command ("python3 -u server.py") picks this up without any image change.
#
# Keep in sync with the upstream server.py when upgrading Holmes chart versions.

# ruff: noqa: E402
import os

from holmes.utils.cert_utils import add_custom_certificate

ADDITIONAL_CERTIFICATE: str = os.environ.get("CERTIFICATE", "")
if add_custom_certificate(ADDITIONAL_CERTIFICATE):
    print("added custom certificate")

# DO NOT ADD ANY IMPORTS OR CODE ABOVE THIS LINE
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional

import colorlog
import litellm
import sentry_sdk
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from litellm.exceptions import AuthenticationError
from starlette.responses import PlainTextResponse

# AG-UI protocol types
from ag_ui.core import (
    AssistantMessage,
    EventType,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder

from holmes import get_version, is_official_release
from holmes.common.env_vars import (
    DEVELOPMENT_MODE,
    ENABLE_CONNECTION_KEEPALIVE,
    ENABLE_TELEMETRY,
    ENABLED_SCHEDULED_PROMPTS,
    HOLMES_HOST,
    HOLMES_PORT,
    LOG_PERFORMANCE,
    MCP_RETRY_BACKOFF_SCHEDULE,
    SENTRY_DSN,
    SENTRY_TRACES_SAMPLE_RATE,
    TOOLSET_STATUS_REFRESH_INTERVAL_SECONDS,
)
from holmes.config import DEFAULT_CONFIG_LOCATION, Config
from holmes.core.conversations import build_chat_messages
from holmes.core.models import (
    ChatRequest,
    ChatResponse,
    FollowUpAction,
    FrontendToolMode,
)
from holmes.core.prompt import PromptComponent
from holmes.core.scheduled_prompts import ScheduledPromptsExecutor
from holmes.core.tools import ToolsetStatusEnum, ToolsetType
from holmes.core.tools_utils.filesystem_result_storage import tool_result_storage
from holmes.core.tools_utils.frontend_tools import (
    build_frontend_noop_tool,
    build_frontend_pause_tool,
)
from holmes.utils.connection_utils import patch_socket_create_connection
from holmes.utils.holmes_status import update_holmes_status_in_db
from holmes.utils.holmes_sync_toolsets import holmes_sync_toolsets_status
from holmes.utils.log import EndpointFilter
from holmes.utils.stream import stream_chat_formatter
from holmes.checks.checks_api import init_checks_app


def init_logging():
    uvicorn_logger = logging.getLogger("uvicorn.access")
    uvicorn_logger.addFilter(EndpointFilter(path="/healthz"))
    uvicorn_logger.addFilter(EndpointFilter(path="/readyz"))

    logging_level = os.environ.get("LOG_LEVEL", "INFO")
    logging_format = "%(log_color)s%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s"
    logging_datefmt = "%Y-%m-%d %H:%M:%S"

    print("setting up colored logging")
    colorlog.basicConfig(
        format=logging_format, level=logging_level, datefmt=logging_datefmt
    )
    logging.getLogger().setLevel(logging_level)

    httpx_logger = logging.getLogger("httpx")
    if httpx_logger:
        httpx_logger.setLevel(logging.WARNING)

    litellm_logger = logging.getLogger("LiteLLM")
    if litellm_logger:
        litellm_logger.handlers = []

    logging.info(f"logger initialized using {logging_level} log level")


init_logging()

if ENABLE_CONNECTION_KEEPALIVE:
    patch_socket_create_connection()


def init_config():
    default_config_path = Path(DEFAULT_CONFIG_LOCATION)
    if default_config_path.exists():
        logging.info(f"Loading config from file: {default_config_path}")
        config = Config.load_from_file(default_config_path)
    else:
        logging.info("No config file found, loading from environment variables")
        config = Config.load_from_env()

    dal = config.dal
    return config, dal


config, dal = init_config()


def sync_before_server_start():
    if not dal.enabled:
        logging.info("Skipping holmes status and toolsets synchronization - not connected to Robusta platform")
        return
    try:
        update_holmes_status_in_db(dal, config)
    except Exception:
        logging.error("Failed to update holmes status", exc_info=True)
    try:
        holmes_sync_toolsets_status(dal, config)
    except Exception:
        logging.error("Failed to synchronise holmes toolsets", exc_info=True)
    if not ENABLED_SCHEDULED_PROMPTS:
        return
    try:
        scheduled_prompts_executor.start()
    except Exception:
        logging.error("Failed to start scheduled prompts executor", exc_info=True)


def _has_failed_mcp_toolsets() -> bool:
    executor = config._server_tool_executor
    if not executor:
        return False
    return any(
        t.type == ToolsetType.MCP and t.status == ToolsetStatusEnum.FAILED
        for t in executor.toolsets
    )


def _get_next_refresh_interval(has_failed_mcp, backoff_index, default_interval):
    if has_failed_mcp and backoff_index < len(MCP_RETRY_BACKOFF_SCHEDULE):
        return MCP_RETRY_BACKOFF_SCHEDULE[backoff_index], backoff_index + 1
    return default_interval, 0


def _toolset_status_refresh_loop():
    interval = TOOLSET_STATUS_REFRESH_INTERVAL_SECONDS
    if interval <= 0:
        logging.info("Periodic toolset status refresh is disabled")
        return

    logging.info(f"Starting periodic toolset status refresh (interval: {interval} seconds)")

    def refresh_loop():
        backoff_index = 0
        while True:
            sleep_time, backoff_index = _get_next_refresh_interval(
                _has_failed_mcp_toolsets(), backoff_index, interval
            )
            if sleep_time < interval:
                logging.info(f"Failed MCP server(s) detected, retrying in {sleep_time} seconds")
            time.sleep(sleep_time)
            try:
                changes = config.refresh_server_tool_executor(dal)
                if changes:
                    for toolset_name, old_status, new_status in changes:
                        logging.info(f"Toolset '{toolset_name}' status changed: {old_status} -> {new_status}")
                    holmes_sync_toolsets_status(dal, config)
                else:
                    logging.debug("Periodic toolset status refresh: no changes detected")
            except Exception:
                logging.error("Error during periodic toolset status refresh", exc_info=True)

    thread = threading.Thread(target=refresh_loop, daemon=True, name="toolset-refresh")
    thread.start()


if ENABLE_TELEMETRY and SENTRY_DSN:
    if is_official_release() or DEVELOPMENT_MODE:
        environment = "production" if is_official_release() else "development"
        version = get_version()
        release = None if version.startswith("dev-") else version
        logging.info(f"Initializing sentry for {environment} environment...")
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            send_default_pii=False,
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            profiles_sample_rate=0,
            environment=environment,
            release=release,
        )
        sentry_sdk.set_tags({
            "account_id": dal.account_id,
            "cluster_name": config.cluster_name,
            "version": get_version(),
            "environment": environment,
        })
    else:
        logging.info("Skipping sentry initialization - not an official release and DEVELOPMENT_MODE not enabled")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if LOG_PERFORMANCE:
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start_time = time.time()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            process_time = int((time.time() - start_time) * 1000)
            status_code = response.status_code if response else "unknown"
            logging.info(f"Request completed {request.method} {request.url.path} status={status_code} latency={process_time}ms")


init_checks_app(app, config)


def already_answered(conversation_history: Optional[List[dict]]) -> bool:
    if conversation_history is None:
        return False
    for message in conversation_history:
        if message["role"] == "assistant":
            return True
    return False


def extract_passthrough_headers(request: Request) -> dict:
    blocked_headers_str = os.environ.get(
        "HOLMES_PASSTHROUGH_BLOCKED_HEADERS", "authorization,cookie,set-cookie"
    )
    blocked_headers = {h.strip().lower() for h in blocked_headers_str.split(",") if h.strip()}
    passthrough_headers = {}
    for header_name, header_value in request.headers.items():
        if header_name.lower() not in blocked_headers:
            passthrough_headers[header_name] = header_value
    return {"headers": passthrough_headers} if passthrough_headers else {}


def _stream_with_storage_cleanup(storage, stream_generator, req_info):
    try:
        yield from stream_generator
    finally:
        logging.info(f"Stream request end: {req_info}")
        storage.__exit__(None, None, None)


@app.post("/api/chat")
def chat(chat_request: ChatRequest, http_request: Request):
    try:
        has_images = bool(chat_request.images)
        has_structured_output = bool(chat_request.response_format)
        req_info = f"/api/chat request: ask={chat_request.ask}"
        logging.info(
            f"Received: {req_info}, model={chat_request.model}, "
            f"images={has_images}, structured_output={has_structured_output}, "
            f"streaming={chat_request.stream}"
        )

        runbooks = config.get_runbook_catalog()

        prompt_component_overrides = None
        if chat_request.behavior_controls:
            logging.info(f"Applying behavior_controls: {chat_request.behavior_controls}")
            prompt_component_overrides = {}
            for k, v in chat_request.behavior_controls.items():
                try:
                    prompt_component_overrides[PromptComponent(k.lower())] = v
                except ValueError:
                    logging.warning(f"Unknown behavior_controls key '{k}', ignoring")

        follow_up_actions = []
        if not already_answered(chat_request.conversation_history):
            follow_up_actions = [
                FollowUpAction(
                    id="logs",
                    action_label="Logs",
                    prompt="Show me the relevant logs",
                    pre_action_notification_text="Fetching relevant logs...",
                ),
                FollowUpAction(
                    id="graphs",
                    action_label="Graphs",
                    prompt="Show me the relevant graphs. Use prometheus and make sure you embed the results with `<< >>` to display a graph",
                    pre_action_notification_text="Drawing some graphs...",
                ),
                FollowUpAction(
                    id="articles",
                    action_label="Articles",
                    prompt="List the relevant runbooks and links used. Write a short summary for each",
                    pre_action_notification_text="Looking up and summarizing runbooks and links...",
                ),
            ]

        request_context = extract_passthrough_headers(http_request)

        storage = tool_result_storage()
        tool_results_dir = storage.__enter__()
        ai = config.create_toolcalling_llm(
            dal=dal, model=chat_request.model, tool_results_dir=tool_results_dir
        )
        global_instructions = dal.get_global_instructions_for_account()
        messages = build_chat_messages(
            chat_request.ask,
            chat_request.conversation_history,
            ai=ai,
            config=config,
            global_instructions=global_instructions,
            additional_system_prompt=chat_request.additional_system_prompt,
            runbooks=runbooks,
            images=chat_request.images,
            prompt_component_overrides=prompt_component_overrides,
        )

        request_ai = ai
        has_pause_tools = False
        if chat_request.frontend_tools:
            backend_tool_names = set(ai.tool_executor.tools_by_name.keys())
            frontend_tool_instances = []
            for ft in chat_request.frontend_tools:
                if ft.name in backend_tool_names:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Frontend tool name '{ft.name}' conflicts with a built-in Holmes tool. Use a different name.",
                    )
                if ft.mode == FrontendToolMode.NOOP:
                    frontend_tool_instances.append(
                        build_frontend_noop_tool(
                            name=ft.name,
                            description=ft.description,
                            parameters=ft.parameters,
                            canned_response=ft.noop_response,
                        )
                    )
                else:
                    has_pause_tools = True
                    frontend_tool_instances.append(
                        build_frontend_pause_tool(
                            name=ft.name,
                            description=ft.description,
                            parameters=ft.parameters,
                        )
                    )

            if has_pause_tools and not chat_request.stream:
                raise HTTPException(
                    status_code=400,
                    detail="frontend_tools with mode='pause' requires stream=true",
                )

            cloned_executor = ai.tool_executor.clone_with_extra_tools(frontend_tool_instances)
            request_ai = ai.with_executor(cloned_executor)

        if chat_request.stream:
            stream = stream_chat_formatter(
                request_ai.call_stream(
                    msgs=messages,
                    enable_tool_approval=chat_request.enable_tool_approval or False,
                    tool_decisions=chat_request.tool_decisions,
                    frontend_tool_results=chat_request.frontend_tool_results,
                    response_format=chat_request.response_format,
                    request_context=request_context,
                ),
                [f.model_dump() for f in follow_up_actions],
            )
            return StreamingResponse(
                _stream_with_storage_cleanup(storage, stream, req_info),
                media_type="text/event-stream",
            )
        else:
            try:
                llm_call = ai.call(
                    messages=messages,
                    trace_span=chat_request.trace_span,
                    response_format=chat_request.response_format,
                    request_context=request_context,
                )
                logging.info(f"Completed {req_info}")
                return ChatResponse(
                    analysis=llm_call.result,
                    tool_calls=llm_call.tool_calls,
                    conversation_history=llm_call.messages,
                    follow_up_actions=follow_up_actions,
                    metadata=llm_call.metadata,
                )
            finally:
                storage.__exit__(None, None, None)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=e.message)
    except litellm.exceptions.RateLimitError as e:
        raise HTTPException(status_code=429, detail=e.message)
    except Exception as e:
        logging.error(f"Error in /api/chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


scheduled_prompts_executor = ScheduledPromptsExecutor(
    dal=dal, config=config, chat_function=chat
)


@app.get("/api/model")
def get_model():
    return {"model_name": json.dumps(config.get_models_list())}


@app.get("/healthz")
def health_check():
    return {"status": "healthy"}


@app.get("/readyz")
def readiness_check():
    try:
        models_list = config.get_models_list()
        return {"status": "ready", "models": models_list}
    except Exception as e:
        logging.error(f"Readiness check failed: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Service not ready")


# ── AG-UI endpoints (Headlamp ai-assistant plugin) ──────────────────────────

@app.get("/api/agui/chat/health")
def agui_chat_health(request: Request):
    return JSONResponse(content="ok")


@app.post("/api/agui/chat")
def agui_chat(input_data: RunAgentInput, request: Request):
    accept_header = request.headers.get("accept", "")
    encoder = EventEncoder(accept=accept_header)

    logging.debug(f"AG-UI context: {input_data.context}")
    if _is_tool_result_message(input_data):
        return PlainTextResponse("OK", status_code=200)

    chat_request = _agui_input_to_holmes_chat_request(input_data=input_data)
    if not chat_request.ask:
        return PlainTextResponse("Bad request. Chat message cannot be empty", status_code=400)

    # Use the same tool executor as /api/chat (server toolsets, not CLI toolsets).
    # create_agui_toolcalling_llm uses list_console_toolsets which has a different
    # tool set than list_server_toolsets, causing tool-not-found failures with local models.
    tool_results_storage = tool_result_storage()
    tool_results_dir = tool_results_storage.__enter__()
    ai = config.create_toolcalling_llm(dal=dal, model=chat_request.model, tool_results_dir=tool_results_dir)
    global_instructions = dal.get_global_instructions_for_account()
    messages = build_chat_messages(
        chat_request.ask,
        chat_request.conversation_history,
        ai=ai,
        config=config,
        global_instructions=global_instructions,
        additional_system_prompt=chat_request.additional_system_prompt,
    )

    from holmes.utils.stream import StreamMessage, StreamEvents

    async def event_generator(message_history):
        try:
            yield encoder.encode(RunStartedEvent(
                type=EventType.RUN_STARTED,
                thread_id=input_data.thread_id,
                run_id=input_data.run_id,
            ))
            hgpt_chat_stream_response: StreamMessage = ai.call_stream(
                msgs=message_history,
                enable_tool_approval=chat_request.enable_tool_approval or False,
            )
            for chunk in hgpt_chat_stream_response:
                if hasattr(chunk, "event"):
                    event_type = chunk.event.value if hasattr(chunk.event, "value") else str(chunk.event)
                else:
                    event_type = "unknown"
                if hasattr(chunk, "data"):
                    tool_name = chunk.data.get("tool_name", chunk.data.get("name", "Tool"))
                    if event_type == StreamEvents.AI_MESSAGE:
                        content = str(chunk.data.get("content", "") or "")
                        if content and not content.strip().startswith("{"):
                            async for event in _stream_agui_text_message_event(content):
                                yield encoder.encode(event)
                    elif event_type == StreamEvents.ANSWER_END:
                        raw = chunk.data.get("content", "") or ""
                        content = str(raw)
                        stripped = content.strip()
                        if stripped.startswith("{"):
                            # Local models (e.g. llama3.1) sometimes return answers
                            # wrapped in a TodoWrite-style JSON blob. Extract the
                            # human-readable "content" field rather than discarding.
                            try:
                                import json as _json
                                parsed = _json.loads(stripped)
                                text = str(parsed.get("content", "") or "")
                                content = text if text and not text.strip().startswith("{") else ""
                            except Exception:
                                content = ""
                        if content:
                            async for event in _stream_agui_text_message_event(content):
                                yield encoder.encode(event)
                    elif event_type == StreamEvents.START_TOOL:
                        if tool_name not in ("TodoWrite", "TodoRead"):
                            async for event in _stream_agui_text_message_event(f"Using tool: `{tool_name}`..."):
                                yield encoder.encode(event)
                    elif event_type == StreamEvents.TOOL_RESULT:
                        if tool_name not in ("TodoWrite", "TodoRead"):
                            result_data = chunk.data.get("result", {})
                            if isinstance(result_data, dict):
                                result_text = result_data.get("data", "")[:300]
                            else:
                                result_text = str(result_data)[:300]
                            if result_text:
                                async for event in _stream_agui_text_message_event(result_text):
                                    yield encoder.encode(event)
            yield encoder.encode(RunFinishedEvent(
                type=EventType.RUN_FINISHED,
                thread_id=input_data.thread_id,
                run_id=input_data.run_id,
            ))
        except Exception as e:
            logging.error(f"Error in /api/agui/chat: {e}", exc_info=True)
            yield encoder.encode(RunErrorEvent(
                type=EventType.RUN_ERROR,
                message=f"Agent encountered an error: {str(e)}",
            ))
        finally:
            tool_results_storage.__exit__(None, None, None)

    return StreamingResponse(event_generator(messages), media_type=encoder.get_content_type())


def _is_tool_result_message(input_data: RunAgentInput) -> bool:
    return len(input_data.messages) > 0 and input_data.messages[-1].role == "tool"


def _agui_input_to_holmes_chat_request(input_data: RunAgentInput) -> ChatRequest:
    non_system_messages = []
    for msg in input_data.messages:
        if msg.role in ("user", "assistant"):
            non_system_messages.append(msg)
        elif msg.role == "tool":
            non_system_messages.append(AssistantMessage(content=msg.content, id=msg.id))

    conversation_history = [{
        "role": "system",
        "content": "You are Holmes, an AI assistant for observability. You use Prometheus metrics, alerts and logs to quickly perform root cause analysis.",
    }]
    if len(non_system_messages) > 1:
        conversation_history.extend([
            {"role": msg.role, "content": msg.content.strip() if msg.content else ""}
            for msg in non_system_messages[:-1]
        ])

    last_user_message = ""
    if non_system_messages and non_system_messages[-1].role == "user":
        last_user_message = non_system_messages[-1].content.strip() if non_system_messages[-1].content else ""

    if input_data.context:
        conversation_history.insert(-1, {
            "role": "system",
            "content": f"The user has the following information in their current web page: {input_data.context}",
        })

    return ChatRequest(
        ask=last_user_message,
        conversation_history=conversation_history,
        model=getattr(input_data, "model", None),
        stream=True,
    )


async def _stream_agui_text_message_event(message: str):
    message_id = str(uuid.uuid4())
    yield TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=message_id, role="assistant")
    yield TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id=message_id, delta=message)
    yield TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=message_id)


# ── main ────────────────────────────────────────────────────────────────────

def main():
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["access"]["fmt"] = "%(asctime)s %(levelname)-8s %(message)s"
    log_config["formatters"]["default"]["fmt"] = "%(asctime)s %(levelname)-8s %(message)s"

    sync_before_server_start()
    _toolset_status_refresh_loop()

    uvicorn.run(app, host=HOLMES_HOST, port=HOLMES_PORT, log_config=log_config)


if __name__ == "__main__":
    main()
