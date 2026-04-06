from fastapi import APIRouter, Request, HTTPException, Response
import base64
import hmac
import hashlib
import json
import logging
import time
from azure.storage.queue import QueueClient

from .config import get_settings

router = APIRouter(prefix="/api", tags=["webhook"])
logger = logging.getLogger(__name__)

# In-memory deduplication: tracks recently enqueued tasks by content hash.
# Key = hash(workItemId + title + description), Value = timestamp.
# Allows re-processing when the title or description changes.
_recent_tasks: dict[str, float] = {}
_DEDUP_WINDOW_SECONDS = 300  # 5 minutes


def _task_hash(work_item_id: str, title: str, description: str) -> str:
    """Create a hash representing the unique content of a task."""
    content = f"{work_item_id}:{title}:{description}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _is_duplicate(work_item_id: str, title: str, description: str) -> bool:
    """Check if this exact task content was recently enqueued. Cleans expired entries."""
    now = time.time()
    # Clean expired entries
    expired = [k for k, ts in _recent_tasks.items() if now - ts > _DEDUP_WINDOW_SECONDS]
    for k in expired:
        del _recent_tasks[k]

    key = _task_hash(work_item_id, title, description)
    if key in _recent_tasks:
        return True

    _recent_tasks[key] = now
    return False


async def verify_webhook(request: Request, body: bytes) -> bool:
    """Verify the Azure DevOps webhook HMAC signature."""
    settings = get_settings()
    if not settings.webhook_secret:
        return True

    signature = request.headers.get("X-Azure-DevOps-Signature", "")
    if not signature:
        return False

    # Azure DevOps sends the HMAC-SHA256 as a base64-encoded value
    expected = hmac.new(
        settings.webhook_secret.encode(),
        body,
        hashlib.sha256
    ).digest()

    expected_b64 = base64.b64encode(expected).decode()

    return hmac.compare_digest(expected_b64, signature)


@router.post("/webhook")
async def receive_webhook(request: Request):
    settings = get_settings()
    body = await request.body()

    if not await verify_webhook(request, body):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info(f"Webhook payload top-level keys: {list(payload.keys())}")

    resource = payload.get("resource", {})
    fields = resource.get("fields", {})
    revision = resource.get("revision", {})
    revision_fields = revision.get("fields", {})

    # Azure DevOps "work item updated" events send only *changed* fields in
    # resource.fields. The full snapshot (including Tags, Title, etc.) lives
    # in resource.revision.fields. Merge both, preferring resource.fields
    # for any key that was just updated.
    merged_fields = {**revision_fields, **fields}

    logger.info(
        f"Webhook work item {resource.get('id')}: "
        f"changed_fields={list(fields.keys())}, "
        f"revision_fields={list(revision_fields.keys())[:10]}..."
    )

    tags = merged_fields.get("System.Tags", "")
    if "ai_item" not in tags:
        logger.info(f"Skipped: No ai_item tag on work item {resource.get('id')}")
        return {"status": "skipped", "message": "No ai_item tag"}

    work_item_id = str(resource.get("id"))
    title = merged_fields.get("System.Title", "")
    description = merged_fields.get("System.Description", "")

    if _is_duplicate(work_item_id, title, description):
        logger.info(f"Skipped duplicate webhook for work item {work_item_id}")
        return {"status": "skipped", "message": "Duplicate webhook, task already enqueued"}

    project_ref = payload.get("projectReference", {})
    project_name = project_ref.get("name", "")

    # Azure DevOps may also send project info under resourceContainers
    if not project_name:
        containers = payload.get("resourceContainers", {})
        project_container = containers.get("project", {})
        # The project ID is available but not the name directly;
        # fall back to System.TeamProject from the work item fields
        project_name = merged_fields.get("System.TeamProject", "")

    logger.info(f"Resolved project name: '{project_name}'")

    repo_url = settings.project_to_repo.get(project_name)
    if not repo_url:
        raise HTTPException(
            status_code=400,
            detail=f"No repository mapping configured for project: {project_name}"
        )

    assigned = merged_fields.get("System.AssignedTo", "")
    if isinstance(assigned, dict):
        assigned = assigned.get("displayName", "")

    task = {
        "workItemId": work_item_id,
        "title": title,
        "description": description,
        "projectName": project_name,
        "assignedTo": assigned,
        "repoUrl": repo_url,
        "retryCount": 0,
    }

    queue_client = QueueClient.from_connection_string(
        settings.storage_connection_string,
        settings.queue_name
    )

    message = json.dumps(task)
    queue_client.send_message(message)

    logger.info(f"Enqueued task for work item: {task['workItemId']}")

    return Response(
        content=json.dumps({"status": "accepted", "taskId": task["workItemId"]}),
        media_type="application/json",
        status_code=202
    )


@router.get("/health")
async def health():
    return {"status": "healthy", "service": "ai-coder"}
