import asyncio
import json
import logging
import os
import shutil
import subprocess
from functools import partial

logger = logging.getLogger(__name__)


def _cleanup_crush_artifacts(repo_path: str):
    """Remove Crush-generated files from the repo directory so they are not committed."""
    cleanup_paths = [
        os.path.join(repo_path, ".crush.json"),
        os.path.join(repo_path, ".crush"),
        os.path.join(repo_path, "AGENTS.md"),
    ]
    for path in cleanup_paths:
        try:
            if os.path.isfile(path):
                os.remove(path)
                logger.debug(f"Cleaned up Crush artifact: {path}")
            elif os.path.isdir(path):
                shutil.rmtree(path)
                logger.debug(f"Cleaned up Crush directory: {path}")
        except OSError as e:
            logger.warning(f"Failed to clean up {path}: {e}")


async def run_coder_task(
    repo_path: str,
    prompt: str,
    azure_openai_endpoint: str,
    azure_openai_key: str,
    azure_openai_deployment: str,
    timeout_seconds: int = 600,
) -> bool:
    """Use Crush (OpenCode successor) to implement code changes agenically.

    Crush is a full agentic coding tool that autonomously:
    - Reads and understands the codebase structure
    - Searches code with grep/glob
    - Edits files with intelligent patching
    - Runs shell commands for testing
    - Uses LSP diagnostics for error checking

    Uses 'crush run' (non-interactive mode) which auto-approves all
    tool calls (file edits, shell commands, etc.).
    Provider prefix: azure/<deployment> for Azure OpenAI models.
    """
    loop = asyncio.get_event_loop()

    def _blocking_task():
        # ----- 1. Write .crush.json config in the repo -----
        crush_config = {
            "$schema": "https://charm.land/crush.json",
        }

        config_path = os.path.join(repo_path, ".crush.json")
        with open(config_path, "w") as f:
            json.dump(crush_config, f, indent=2)
        logger.info(f"Created .crush.json config at {config_path}")

        # ----- 2. Build environment with Azure OpenAI creds -----
        env = os.environ.copy()
        # Map our config var names to Crush's expected env vars
        env["AZURE_OPENAI_API_ENDPOINT"] = azure_openai_endpoint
        env["AZURE_OPENAI_API_KEY"] = azure_openai_key
        env["AZURE_OPENAI_API_VERSION"] = "2024-02-01"
        # Suppress interactive/color output
        env["NO_COLOR"] = "1"
        env["CRUSH_DISABLE_METRICS"] = "1"

        # ----- 3. Build the prompt -----
        task_prompt = (
            f"Implement the following feature in this codebase:\n\n"
            f"{prompt}\n\n"
            f"Instructions:\n"
            f"1. Analyze the codebase structure and existing patterns\n"
            f"2. Implement the feature described above\n"
            f"3. Write appropriate tests if test patterns exist in the project\n"
            f"4. Follow existing code conventions and patterns\n"
            f"5. Do NOT modify files containing secrets, credentials, or API keys\n"
            f"6. Do NOT run git commit or git push — only modify/create source files\n"
        )

        # ----- 4. Run Crush in non-interactive mode -----
        #   crush run "prompt" -q -c <repo> -m <model>
        #   Non-interactive mode auto-approves all permissions
        #   -q      = suppress spinner output
        #   -c      = set working directory to the repo
        #   -m      = select model (provider.deployment)
        cmd = [
            "crush",
            "run",
            "--quiet",
            "-c", repo_path,
            "-m", f"azure/{azure_openai_deployment}",
            task_prompt,
        ]

        logger.info(
            f"Running Crush (agentic coding) in non-interactive mode on {repo_path}"
        )

        try:
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
            )

            logger.info(f"Crush exit code: {result.returncode}")
            if result.stdout:
                logger.info(f"Crush output:\n{result.stdout[:3000]}")
            if result.stderr:
                logger.warning(f"Crush stderr:\n{result.stderr[:1000]}")

            return result.returncode == 0

        except subprocess.TimeoutExpired:
            logger.error(f"Crush timed out after {timeout_seconds}s")
            return False
        except FileNotFoundError:
            logger.error(
                "Crush CLI not found. Install via: "
                "npm install -g @charmland/crush  or  "
                "brew install charmbracelet/tap/crush"
            )
            raise RuntimeError("Crush CLI not found in PATH")
        finally:
            # Always clean up Crush artifacts to avoid committing them
            _cleanup_crush_artifacts(repo_path)

    return await loop.run_in_executor(None, _blocking_task)
