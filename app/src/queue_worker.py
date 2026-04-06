import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
from functools import partial

from azure.storage.queue import QueueClient

from .config import get_settings
from .azure_devops import AzureDevOpsClient
from .coder import run_coder_task

logger = logging.getLogger(__name__)


def _run_git(args: list[str], cwd: str, timeout: int = 120, env: dict = None) -> subprocess.CompletedProcess:
    """Run a git command safely, avoiding PAT leaks in logs."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return result
    except subprocess.CalledProcessError as e:
        # Sanitize stderr/stdout to avoid leaking PAT from clone URLs
        sanitized_stderr = _sanitize_output(e.stderr or "")
        sanitized_stdout = _sanitize_output(e.stdout or "")
        logger.error(f"Git command failed: git {' '.join(args[:2])}... stderr={sanitized_stderr}")
        raise subprocess.CalledProcessError(
            e.returncode, e.cmd, output=sanitized_stdout, stderr=sanitized_stderr
        ) from None


def _sanitize_output(text: str) -> str:
    """Remove PAT tokens from output strings."""
    return re.sub(r"://[^/@]*@", "://<REDACTED>@", text)


async def _run_in_executor(func, *args, **kwargs):
    """Run a blocking function in a thread executor to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


async def process_task(task: dict) -> bool:
    settings = get_settings()
    work_item_id = task["workItemId"]
    title = task["title"]
    description = task["description"]
    repo_url = task["repoUrl"]
    project_name = task["projectName"]

    logger.info(f"Processing task: {work_item_id} - {title}")

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = os.path.join(tmpdir, "repo")

        # Use GIT_ASKPASS to provide credentials without embedding PAT in the URL.
        # Write PAT to a file and cat it to avoid shell injection via special chars.
        pat_file = os.path.join(tmpdir, ".git-pat")
        with open(pat_file, "w") as f:
            f.write(settings.azure_devops_pat)
        os.chmod(pat_file, 0o600)

        askpass_script = os.path.join(tmpdir, "git-askpass.sh")
        with open(askpass_script, "w") as f:
            f.write(f"#!/bin/sh\ncat '{pat_file}'\n")
        os.chmod(askpass_script, 0o700)

        clone_env = os.environ.copy()
        clone_env["GIT_ASKPASS"] = askpass_script
        clone_env["GIT_TERMINAL_PROMPT"] = "0"

        # Construct auth URL for clone (user is empty for PAT auth)
        auth_url = repo_url.replace("https://", "https://pat@")

        try:
            await _run_in_executor(
                subprocess.run,
                ["git", "clone", auth_url, repo_path],
                check=True,
                capture_output=True,
                timeout=60,
                env=clone_env,
            )
            logger.info(f"Cloned repository to {repo_path}")
        except subprocess.CalledProcessError as e:
            sanitized = _sanitize_output(e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or ""))
            logger.error(f"Git clone failed: {sanitized}")
            raise

        await _run_in_executor(
            _run_git, ["config", "user.email", "ai-coder@company.com"], cwd=repo_path
        )
        await _run_in_executor(
            _run_git, ["config", "user.name", "AI Coder"], cwd=repo_path
        )

        # Sanitize title into a valid branch slug
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()[:50]
        branch_name = f"feature/{work_item_id}-{slug}"
        try:
            await _run_in_executor(
                _run_git, ["checkout", "-b", branch_name], cwd=repo_path
            )
        except subprocess.CalledProcessError:
            await _run_in_executor(
                _run_git, ["checkout", branch_name], cwd=repo_path
            )

        prompt = f"""
You are an AI coding assistant. Implement the following feature in this repository.

## Work Item
Title: {title}
Description: {description}

## Instructions
1. Analyze the codebase to understand the existing structure and language
2. Implement the feature described above by creating or modifying files as needed
3. If the repository is empty or has no source code, create the necessary files from scratch using an appropriate language and framework
4. Write appropriate tests if test patterns exist
5. Follow existing code conventions and patterns
6. DO NOT commit changes - just create/modify source files
7. DO NOT ask questions - just implement the feature to the best of your ability
"""

        try:
            success = await run_coder_task(
                repo_path=repo_path,
                prompt=prompt,
                azure_openai_endpoint=settings.azure_openai_endpoint,
                azure_openai_key=settings.azure_openai_key,
                azure_openai_deployment=settings.azure_openai_deployment,
                timeout_seconds=600,
            )

            if not success:
                logger.warning(f"Task {work_item_id} did not complete within timeout")
                return False

        except Exception as e:
            logger.error(f"Coder task failed: {e}")
            raise

        # Check for changes
        result = await _run_in_executor(
            subprocess.run,
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )

        if not result.stdout.strip():
            logger.info(f"No changes made for task {work_item_id}")
            return True

        # Stage only tracked/modified files, not untracked files that may contain secrets
        await _run_in_executor(
            _run_git, ["add", "-u"], cwd=repo_path
        )

        # Check for new files and add them selectively (exclude common secret patterns)
        untracked_result = await _run_in_executor(
            subprocess.run,
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if untracked_result.stdout.strip():
            safe_files = []
            for f in untracked_result.stdout.strip().split("\n"):
                lower = f.lower()
                if any(pat in lower for pat in [".env", "secret", "credential", ".key", ".pem", "token", ".crush"]):
                    logger.warning(f"Skipping potentially sensitive file: {f}")
                    continue
                safe_files.append(f)
            if safe_files:
                await _run_in_executor(
                    _run_git, ["add"] + safe_files, cwd=repo_path
                )

        commit_message = f"""AI Coder: {title}

Work Item: #{work_item_id}
Project: {project_name}

{description[:500]}"""

        await _run_in_executor(
            _run_git, ["commit", "-m", commit_message], cwd=repo_path
        )

        try:
            await _run_in_executor(
                _run_git, ["push", "--force", "-u", "origin", branch_name], cwd=repo_path, timeout=120, env=clone_env
            )
            logger.info(f"Pushed branch: {branch_name}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Git push failed: {_sanitize_output(str(e.stderr))}")
            raise

        # Create PR
        ado_client = AzureDevOpsClient(
            org=settings.azure_devops_org,
            pat=settings.azure_devops_pat,
        )

        pr_description = f"""## AI-Generated Pull Request

**Work Item:** #{work_item_id}
**Project:** {project_name}

### Description
{description}

### Changes Made
This PR was automatically generated by the AI Coder service.

---
*This PR was created automatically. Please review the changes before merging.*
"""

        try:
            pr = await ado_client.create_pull_request(
                repo_url=repo_url,
                source_branch=branch_name,
                target_branch="main",
                title=f"[AI] {title}",
                description=pr_description,
                work_item_id=work_item_id,
            )

            logger.info(f"Created PR: {pr.get('pullRequestId')}")

            await ado_client.update_work_item(
                work_item_id=work_item_id,
                project_name=project_name,
                fields={
                    "System.State": "Resolved",
                    "System.History": f"AI Coder created PR #{pr.get('pullRequestId')}",
                },
            )

        except Exception as e:
            logger.error(f"Failed to create PR: {e}")
            raise

    return True


async def start_queue_consumer():
    settings = get_settings()
    queue_client = QueueClient.from_connection_string(
        settings.storage_connection_string,
        settings.queue_name
    )

    dlq_client = QueueClient.from_connection_string(
        settings.storage_connection_string,
        settings.dead_letter_queue_name
    )

    logger.info("Starting queue consumer...")

    while True:
        try:
            messages = list(queue_client.receive_messages(
                max_messages=1,
                visibility_timeout=300,
            ))

            if not messages:
                await asyncio.sleep(5)
                continue

            for message in messages:
                task = json.loads(message.content)
                retry_count = task.get("retryCount", 0)

                logger.info(
                    f"Processing task: {task.get('workItemId')} "
                    f"(attempt {retry_count + 1}/{settings.max_retries})"
                )

                try:
                    await process_task(task)
                    queue_client.delete_message(message)
                    logger.info(f"Task completed: {task.get('workItemId')}")

                except Exception as e:
                    logger.error(f"Task failed: {e}")

                    if retry_count < settings.max_retries - 1:
                        task["retryCount"] = retry_count + 1
                        queue_client.delete_message(message)
                        queue_client.send_message(json.dumps(task))
                        logger.info(f"Requeued task: {task.get('workItemId')}")
                    else:
                        queue_client.delete_message(message)
                        dlq_client.send_message(message.content)
                        logger.error(
                            f"Task moved to DLQ: {task.get('workItemId')}"
                        )

        except Exception as e:
            logger.error(f"Queue consumer error: {e}")
            await asyncio.sleep(5)
