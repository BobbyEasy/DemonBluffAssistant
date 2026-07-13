from __future__ import annotations

import json
import re
from importlib.resources import files

from pydantic import BaseModel


class RoleDefinition(BaseModel):
    role_id: str
    name_en: str
    name_zh: str
    aliases: list[str] = []
    alignment: str
    character_type: str
    ability_kind: str
    lie_rule: str
    description_en: str


def _normalize(value: str) -> str:
    return re.sub(r"[\s_\-']+", "", value).casefold()


class RoleCatalog:
    def __init__(self, roles: dict[str, RoleDefinition], version: str) -> None:
        self.roles = roles
        self.version = version
        self._aliases: dict[str, str] = {}
        for role in roles.values():
            for alias in [role.role_id, role.name_en, role.name_zh, *role.aliases]:
                self._aliases[_normalize(alias)] = role.role_id

    @classmethod
    def load_default(cls) -> "RoleCatalog":
        path = files("demon_bluff_assistant").joinpath("role_catalog.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        roles = {
            item["role_id"]: RoleDefinition.model_validate(item)
            for item in payload["roles"]
        }
        return cls(roles=roles, version=payload["version"])

    def resolve(self, value: str) -> RoleDefinition:
        try:
            return self.roles[self._aliases[_normalize(value)]]
        except KeyError as exc:
            raise KeyError(f"unknown role: {value}") from exc

