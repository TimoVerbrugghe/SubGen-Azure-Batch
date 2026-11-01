"""Model Context Protocol tools for agent-managed transcription batches."""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.agent_api import (
    AgentBatchCancelResponse,
    AgentBatchListResponse,
    AgentBatchStatusResponse,
    AgentBatchSubmitRequest,
    AgentBatchSubmitResponse,
    cancel_agent_batch,
    get_agent_batch_status,
    list_active_agent_batches,
    submit_agent_batch,
)
from app.config import get_settings

_allowed_hosts = get_settings().agent_mcp_allowed_hosts

mcp_server = FastMCP(
    "SubGen-Azure-Batch",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(_allowed_hosts),
        allowed_hosts=_allowed_hosts,
        allowed_origins=[
            f"{scheme}://{host}"
            for host in _allowed_hosts
            for scheme in ("http", "https")
        ],
    ),
)


@mcp_server.tool()
async def submit_transcription_batch(
    languages: list[str],
    files: list[str] | None = None,
    folders: list[str] | None = None,
    apply_skip_rules: bool = True,
    notify_bazarr: bool = False,
) -> AgentBatchSubmitResponse:
    """Queue each supported media file for each language in the requested batch.

    Paths must be absolute container paths under configured MEDIA_FOLDERS.
    Languages are ISO 639 codes or Azure locales. At most four languages and
    1000 file-language jobs are accepted per request.
    """
    request = AgentBatchSubmitRequest(
        files=files or [],
        folders=folders or [],
        languages=languages,
        apply_skip_rules=apply_skip_rules,
        notify_bazarr=notify_bazarr,
    )
    response = await submit_agent_batch(request)
    return response


@mcp_server.tool()
async def list_active_batches() -> AgentBatchListResponse:
    """List agent-submitted batches with queued or in-progress jobs."""
    response = await list_active_agent_batches()
    return response


@mcp_server.tool()
async def get_batch_status(session_id: str) -> AgentBatchStatusResponse:
    """Get batch counts, skipped files, and detailed per-file processing stages."""
    response = await get_agent_batch_status(session_id)
    return response


@mcp_server.tool()
async def cancel_batch(session_id: str) -> AgentBatchCancelResponse:
    """Cancel a batch, stop its worker task, and clean up Azure resources."""
    return await cancel_agent_batch(session_id)
