"""Gitea integration – collects repository and user stats."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class GiteaAPI:
    def __init__(self, host: str, token: str | None = None, verify_ssl: bool = False):
        self.base = host.rstrip("/")
        self.token = token
        self.verify_ssl = verify_ssl

    def _headers(self) -> dict:
        if self.token:
            return {"Authorization": f"token {self.token}"}
        return {}

    async def fetch_all(self) -> dict:
        """Fetch Gitea version, repos, users, and orgs."""
        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=15) as client:
            version_resp = await client.get(
                f"{self.base}/api/v1/version", headers=self._headers()
            )
            version_resp.raise_for_status()
            version_info = version_resp.json()

            repos_resp = await client.get(
                f"{self.base}/api/v1/repos/search",
                params={"limit": 50, "page": 1},
                headers=self._headers(),
            )
            repos_resp.raise_for_status()
            repos_body = repos_resp.json()
            repos = repos_body.get("data", repos_body) if isinstance(repos_body, dict) else repos_body

            # Admin endpoints — may return 403 for non-admin tokens
            users: list = []
            orgs: list = []
            try:
                users_resp = await client.get(
                    f"{self.base}/api/v1/admin/users",
                    params={"limit": 50},
                    headers=self._headers(),
                )
                if users_resp.status_code == 200:
                    users = users_resp.json()
            except Exception:
                pass

            try:
                orgs_resp = await client.get(
                    f"{self.base}/api/v1/admin/orgs",
                    params={"limit": 50},
                    headers=self._headers(),
                )
                if orgs_resp.status_code == 200:
                    orgs = orgs_resp.json()
            except Exception:
                pass

        return {
            "version_info": version_info,
            "repos": repos if isinstance(repos, list) else [],
            "users": users if isinstance(users, list) else [],
            "orgs": orgs if isinstance(orgs, list) else [],
        }

    async def health_check(self) -> bool:
        """Return True if the Gitea API is reachable."""
        try:
            async with httpx.AsyncClient(verify=self.verify_ssl, timeout=8) as client:
                resp = await client.get(
                    f"{self.base}/api/v1/version", headers=self._headers()
                )
                return resp.status_code == 200
        except Exception:
            return False


def parse_gitea_data(version_info: dict, repos: list, users: list, orgs: list) -> dict:
    """
    Parse Gitea API responses into a structured summary dict.

    Returns:
    {
      "version": str,
      "repos_total": int,
      "repos_public": int,
      "repos_private": int,
      "repos": [{"name", "full_name", "description", "stars", "forks", "open_issues", "updated_at", "private"}],
      "users_total": int,
      "orgs_total": int,
    }
    """
    version = version_info.get("version", "unknown")

    repo_list = []
    public_count = 0
    private_count = 0

    for repo in repos:
        is_private = repo.get("private", False)
        if is_private:
            private_count += 1
        else:
            public_count += 1

        repo_list.append({
            "name": repo.get("name", ""),
            "full_name": repo.get("full_name", ""),
            "description": repo.get("description") or "",
            "stars": repo.get("stars_count", repo.get("stargazers_count", 0)),
            "forks": repo.get("forks_count", 0),
            "open_issues": repo.get("open_issues_count", 0),
            "updated_at": repo.get("updated", repo.get("updated_at", "")),
            "private": is_private,
        })

    # Sort by updated_at descending
    repo_list.sort(key=lambda r: r["updated_at"] or "", reverse=True)

    return {
        "version": version,
        "repos_total": len(repos),
        "repos_public": public_count,
        "repos_private": private_count,
        "repos": repo_list,
        "users_total": len(users),
        "orgs_total": len(orgs),
    }


async def collect_gitea_instance(instance_id: int, db: "AsyncSession") -> None:
    """Fetch and store a snapshot for a single Gitea instance."""
    import json
    from datetime import datetime

    from database import GiteaInstance, GiteaSnapshot, decrypt_value

    instance = await db.get(GiteaInstance, instance_id)
    if not instance:
        return

    token = decrypt_value(instance.token_enc) if instance.token_enc else None
    api = GiteaAPI(host=instance.host, token=token, verify_ssl=instance.verify_ssl)

    try:
        raw = await api.fetch_all()
        data = parse_gitea_data(
            raw["version_info"], raw["repos"], raw["users"], raw["orgs"]
        )
        db.add(GiteaSnapshot(
            instance_id=instance_id,
            timestamp=datetime.utcnow(),
            ok=True,
            data_json=json.dumps(data),
            error=None,
        ))
    except Exception as exc:
        logger.error("Gitea collect [%s]: %s", instance.name, exc)
        db.add(GiteaSnapshot(
            instance_id=instance_id,
            timestamp=datetime.utcnow(),
            ok=False,
            data_json=None,
            error=str(exc),
        ))
    await db.commit()
