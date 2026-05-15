"""Map ContextPackage to/from A2A Protocol constructs.

Reference: https://github.com/google/A2A
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from handoff.models.package import ContextPackage


def _get_a2a_types() -> Any:
    """Lazy import a2a types to avoid hard dependency."""
    try:
        from a2a import types as a2a_types
        return a2a_types
    except ImportError as exc:
        raise ImportError(
            "A2A integration requires 'a2a-sdk'. Install with: "
            "pip install agent-context-handoff[a2a]"
        ) from exc


def to_a2a_task(package: "ContextPackage") -> dict[str, Any]:
    """Convert a ContextPackage to an A2A Task dictionary.

    This is a framework-agnostic dict representation that can be
    consumed by the a2a-sdk Client or sent over HTTP directly.
    """
    from handoff.serialization.serializer import JsonSerializer

    serializer = JsonSerializer()
    payload = serializer.serialize(package).decode("utf-8")

    return {
        "id": package.meta.package_id,
        "sessionId": package.meta.trace_id,
        "status": {
            "state": "input-required",
            "message": {
                "role": "agent",
                "parts": [
                    {"type": "text", "text": package.task.progress_summary.to_markdown()}
                ],
            },
        },
        "artifacts": [
            {
                "name": "context-package",
                "parts": [
                    {
                        "type": "data",
                        "data": {
                            "mimeType": "application/json",
                            "content": payload,
                        },
                    }
                ],
                "metadata": {
                    "handoff_reason": package.meta.handoff_reason.value,
                    "priority": package.meta.priority.value,
                    "source_agent": package.meta.source.agent_id,
                    "spec_version": package.meta.spec_version,
                    "required_capabilities": package.task.required_capabilities,
                },
            }
        ],
        "metadata": {
            "operation": "handoff_resume",
            "original_task_id": package.task.original_task_id,
        },
    }


def from_a2a_task(task: dict[str, Any]) -> "ContextPackage":
    """Extract a ContextPackage from an A2A Task dictionary.

    Args:
        task: A2A Task dict containing a context-package artifact.

    Returns:
        Reconstructed ContextPackage.

    Raises:
        ValueError: If the task does not contain a valid context package.
    """
    from handoff.serialization.serializer import JsonSerializer

    artifacts = task.get("artifacts", [])
    handoff_artifact = None
    for art in artifacts:
        if art.get("name") == "context-package":
            handoff_artifact = art
            break

    if handoff_artifact is None:
        raise ValueError("Task does not contain a context-package artifact")

    parts = handoff_artifact.get("parts", [])
    if not parts:
        raise ValueError("Context artifact has no parts")

    data = parts[0].get("data", {})
    payload = data.get("content", "")

    serializer = JsonSerializer()
    return serializer.deserialize(payload.encode("utf-8"))


def build_agent_card(
    name: str,
    url: str,
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Build an A2A Agent Card for a handoff-capable agent.

    Args:
        name: Agent display name.
        url: Endpoint URL for the agent.
        capabilities: Optional list of capability tags.

    Returns:
        Agent Card dictionary.
    """
    skills = [
        {
            "id": "context-handoff",
            "name": "Context Handoff",
            "description": (
                "Can receive and resume tasks from other agents "
                "via context packages"
            ),
        }
    ]
    if capabilities:
        skills.append({
            "id": "custom-capabilities",
            "name": "Custom Capabilities",
            "description": f"Supports: {', '.join(capabilities)}",
        })

    return {
        "name": name,
        "description": "Agent capable of receiving and resuming handed-off tasks",
        "url": url,
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "skills": skills,
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
    }


class A2AHandoffClient:
    """High-level client for sending handoffs via A2A protocol.

    Wraps the a2a-sdk Client to provide handoff-specific operations.
    """

    def __init__(self, client: Any) -> None:
        """Initialize with an a2a-sdk Client instance.

        Args:
            client: Configured a2a.client.Client instance.
        """
        self._client = client

    async def send_handoff(self, package: "ContextPackage") -> dict[str, Any]:
        """Send a context handoff as an A2A Task.

        Args:
            package: The context package to send.

        Returns:
            A2A Task response dictionary.
        """
        task_dict = to_a2a_task(package)
        response: dict[str, Any] = await self._client.send_message(task_dict)
        return response

    async def get_handoff_status(self, package_id: str) -> dict[str, Any]:
        """Query the status of a previously sent handoff.

        Args:
            package_id: The package/task ID to query.

        Returns:
            Task status dictionary.
        """
        status: dict[str, Any] = await self._client.get_task(package_id)
        return status
