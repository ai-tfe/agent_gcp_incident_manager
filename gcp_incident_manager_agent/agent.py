"""GCP Incident Manager Agent with Cloud Monitoring Alerts, Cloud Logging, Cloud Support Cases, and GCP Knowledge MCP endpoints."""

import logging
import os
from typing import List, Optional

import google.auth
import google.auth.transport.requests
from google.adk.agents.llm_agent import Agent
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StreamableHTTPConnectionParams,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

logger = logging.getLogger(__name__)

MODEL = os.getenv("ADK_MODEL", "gemini-2.5-flash")

# Managed Google Cloud MCP Endpoints (routed via Agent Egress Gateway)
MONITORING_MCP_URL = os.getenv("MONITORING_MCP_URL", "https://monitoring.googleapis.com/mcp")
LOGGING_MCP_URL = os.getenv("LOGGING_MCP_URL", "https://logging.googleapis.com/mcp")
SUPPORT_MCP_URL = os.getenv("SUPPORT_MCP_URL", "https://cloudsupport.googleapis.com/mcp")
KNOWLEDGE_MCP_URL = os.getenv("KNOWLEDGE_MCP_URL", "https://developerknowledge.googleapis.com/mcp")


def _gcp_agent_identity_headers(_readonly_context=None) -> dict[str, str]:
    """Attach AGENT_IDENTITY OAuth Bearer token and resolved string project ID header to remote MCP calls."""
    headers: dict[str, str] = {}
    try:
        credentials, default_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(google.auth.transport.requests.Request())
        if credentials.token:
            headers["Authorization"] = f"Bearer {credentials.token}"
        raw_proj = (
            os.getenv("GOOGLE_CLOUD_QUOTA_PROJECT")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or default_project
            or "svcprjprd"
        )
        quota_project = "svcprjprd" if str(raw_proj).isdigit() else str(raw_proj)
        headers["X-Goog-User-Project"] = quota_project
    except Exception as exc:
        logger.warning("Failed to refresh AGENT_IDENTITY credentials for MCP call: %s", exc)
    return headers


class BestEffortMcpToolset(McpToolset):
    """Loads filtered MCP tools with AGENT_IDENTITY auth and keeps the agent running if egress denies a server."""

    def __init__(
        self,
        *,
        server_name: str,
        url: str,
        tool_name_prefix: str,
        tool_filter: Optional[List[str]] = None,
    ):
        self._server_name = server_name
        super().__init__(
            connection_params=StreamableHTTPConnectionParams(
                url=url,
                timeout=30.0,
            ),
            tool_filter=tool_filter,
            tool_name_prefix=tool_name_prefix,
            errlog=None,
            header_provider=_gcp_agent_identity_headers,
        )

    async def get_tools(self, readonly_context=None):
        try:
            return await super().get_tools(readonly_context)
        except Exception as exc:
            logger.warning("MCP server %s unavailable: %s", self._server_name, exc)
            return []


# ---------------------------------------------------------------------------
# 1. Google Cloud Monitoring MCP (Alerts, Incidents, Policies & Metrics Active)
# ---------------------------------------------------------------------------
monitoring_mcp_toolset = BestEffortMcpToolset(
    server_name="google-cloud-monitoring",
    url=MONITORING_MCP_URL,
    tool_name_prefix="monitoring_",
    tool_filter=[
        # --- ACTIVE: Incident / Alert Triage & Metrics Inspection ---
        "list_alerts",
        "get_alert",
        "list_alert_policies",
        "get_alert_policy",
        "list_timeseries",
        "query_range",
        # --- FUTURE / DISABLED MONITORING TOOLS (Uncomment to enable) ---
        # "list_metric_descriptors",
        # "list_dashboards",
        # "get_dashboard",
    ],
)

# ---------------------------------------------------------------------------
# 2. Google Cloud Logging MCP (Error & Audit Log Correlation Active)
# ---------------------------------------------------------------------------
logging_mcp_toolset = BestEffortMcpToolset(
    server_name="google-cloud-logging",
    url=LOGGING_MCP_URL,
    tool_name_prefix="logging_",
    tool_filter=[
        # --- ACTIVE: Read-Only Log Inspection ---
        "list_log_entries",
        "list_log_names",
        # --- FUTURE / DISABLED LOGGING TOOLS (Uncomment to enable) ---
        # "get_bucket",
        # "list_buckets",
        # "get_view",
        # "list_views",
    ],
)

# ---------------------------------------------------------------------------
# 3. Google Cloud Support MCP (Support Case & Incident Comment Triage Active)
# ---------------------------------------------------------------------------
support_mcp_toolset = BestEffortMcpToolset(
    server_name="google-cloud-support",
    url=SUPPORT_MCP_URL,
    tool_name_prefix="support_",
    tool_filter=[
        # --- ACTIVE: Support Case & Comment Triage ---
        "search_cases",
        "get_case",
        "list_comments",
        # --- FUTURE / DISABLED CLOUD SUPPORT TOOLS (Uncomment to enable) ---
        # "get_comment",
        # "list_attachments",
        # "get_attachment",
        # "TitanCheckSupportEligibility",
    ],
)

# ---------------------------------------------------------------------------
# 4. Google Cloud Knowledge / Documentation MCP (Runbooks & Best Practices)
# ---------------------------------------------------------------------------
knowledge_mcp_toolset = BestEffortMcpToolset(
    server_name="google-developer-knowledge",
    url=KNOWLEDGE_MCP_URL,
    tool_name_prefix="knowledge_",
    tool_filter=[
        # --- ACTIVE: GCP Documentation & Remediation Lookup ---
        "search_documents",
        "get_documents",
        # --- FUTURE / DISABLED KNOWLEDGE TOOLS (Uncomment to enable) ---
        # "answer_query",
    ],
)


root_agent = Agent(
    model=MODEL,
    name="gcp_incident_manager_agent",
    description=(
        "Google Cloud Incident Manager Agent providing alert and incident triage, "
        "Cloud Logging root-cause correlation, Cloud Support case inspection, and GCP remediation runbooks."
    ),
    instruction=(
        "You are the Google Cloud Incident Manager Specialist (`gcp_incident_manager_agent`).\n"
        "Follow a Detect -> Correlate -> Consult Runbooks -> Recommend workflow:\n"
        "1. DETECT & TRIAGE: Use Cloud Monitoring tools (`monitoring_*`) to inspect active alerts (`list_alerts`, `get_alert`), "
        "alert policies (`list_alert_policies`, `get_alert_policy`), and anomalous metric time series (`list_timeseries`, `query_range`).\n"
        "2. CORRELATE: Use Cloud Logging tools (`logging_*`) to correlate error/audit log entries around the incident window, "
        "and Cloud Support tools (`support_*`) to inspect related support cases and comments.\n"
        "3. CONSULT: Use GCP Knowledge tools (`knowledge_*`) to look up official Google Cloud troubleshooting guides and remediation best practices.\n"
        "Never claim an action was executed unless a tool result proves it. If an MCP server is blocked by an "
        "Agent Egress Gateway policy, report which server was denied and continue with the available tools."
    ),
    tools=[
        monitoring_mcp_toolset,
        logging_mcp_toolset,
        support_mcp_toolset,
        knowledge_mcp_toolset,
    ],
)
