import base64
import httpx
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class AzureDevOpsClient:
    def __init__(self, org: str, pat: str):
        self.org = org
        self.pat = pat
        self.base_url = f"https://dev.azure.com/{org}"
        # Azure DevOps PATs require Basic auth with empty username
        credentials = base64.b64encode(f":{pat}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        }

    def parse_repo_url(self, repo_url: str) -> tuple[str, str, str]:
        parts = repo_url.replace(f"https://dev.azure.com/{self.org}/", "").split("/_git/")
        project = parts[0]
        repo = parts[1] if len(parts) > 1 else ""
        return self.org, project, repo

    async def create_pull_request(
        self,
        repo_url: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
        work_item_id: Optional[str] = None
    ) -> Dict[str, Any]:
        _, project, repo = self.parse_repo_url(repo_url)

        pr_data = {
            "sourceRefName": f"refs/heads/{source_branch}",
            "targetRefName": f"refs/heads/{target_branch}",
            "title": title,
            "description": description,
            "reviewers": [],
        }

        if work_item_id:
            pr_data["workItemRefs"] = [{"id": str(work_item_id)}]

        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{self.base_url}/{project}/_apis/git/repos/{repo}/pullrequests?api-version=7.0"
            response = await client.post(url, json=pr_data, headers=self.headers)

            if response.status_code in (200, 201):
                pr = response.json()
                logger.info(f"Created PR: {pr.get('pullRequestId')} - {pr.get('url')}")
                return pr
            else:
                error = response.text
                logger.error(f"Failed to create PR: {response.status_code} - {error}")
                raise Exception(f"PR creation failed: {error}")

    async def add_pr_comment(
        self,
        repo_url: str,
        pr_id: int,
        comment: str
    ) -> None:
        _, project, repo = self.parse_repo_url(repo_url)

        comment_data = {
            "comments": [
                {
                    "parentCommentId": 0,
                    "content": comment,
                    "commentType": 1,
                }
            ],
            "status": 1,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{self.base_url}/{project}/_apis/git/repos/{repo}/pullRequests/{pr_id}/threads?api-version=7.0"
            response = await client.post(url, json=comment_data, headers=self.headers)

            if response.status_code not in (200, 201):
                logger.warning(f"Failed to add PR comment: {response.text}")

    async def update_work_item(
        self,
        work_item_id: str,
        project_name: str,
        fields: Dict[str, Any]
    ) -> None:
        patch_data = [
            {"op": "add", "path": f"/fields/{k}", "value": v}
            for k, v in fields.items()
        ]

        # Work item PATCH requires application/json-patch+json content type
        headers = {**self.headers, "Content-Type": "application/json-patch+json"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{self.base_url}/{project_name}/_apis/wit/workitems/{work_item_id}?api-version=7.0"
            response = await client.patch(url, json=patch_data, headers=headers)

            if response.status_code not in (200, 201):
                logger.warning(f"Failed to update work item: {response.text}")
