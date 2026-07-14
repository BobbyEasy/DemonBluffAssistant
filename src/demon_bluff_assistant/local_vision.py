from __future__ import annotations

import math
import re
import threading
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from io import BytesIO
from typing import Protocol

from PIL import Image

from demon_bluff_assistant.models import GameState, SeatState, StatePatch, VillageConfig, VillageSetupSuggestion
from demon_bluff_assistant.roles import RoleCatalog, RoleDefinition


class LocalRecognitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class OcrToken:
    text: str
    confidence: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2, (self.top + self.bottom) / 2)


@dataclass(frozen=True)
class OcrDocument:
    width: int
    height: int
    tokens: list[OcrToken]


class OcrEngine(Protocol):
    def recognize(self, png_bytes: bytes) -> OcrDocument: ...


class RapidOCREngine:
    def __init__(self) -> None:
        self._engine = None
        self._lock = threading.Lock()

    def _get_engine(self):
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def recognize(self, png_bytes: bytes) -> OcrDocument:
        try:
            import cv2
            import numpy as np

            image = cv2.imdecode(
                np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR
            )
            if image is None:
                raise LocalRecognitionError("截图不是有效的 PNG 图像。")
            height, width = image.shape[:2]
            with self._lock:
                result = self._get_engine()(image)
            tokens = []
            boxes = result.boxes if result.boxes is not None else []
            texts = result.txts if result.txts is not None else []
            scores = result.scores if result.scores is not None else []
            for box, text, score in zip(boxes, texts, scores):
                points = list(box)
                xs = [float(point[0]) for point in points]
                ys = [float(point[1]) for point in points]
                tokens.append(
                    OcrToken(
                        text=str(text).strip(),
                        confidence=float(score),
                        left=min(xs),
                        top=min(ys),
                        right=max(xs),
                        bottom=max(ys),
                    )
                )
            return OcrDocument(width=width, height=height, tokens=tokens)
        except LocalRecognitionError:
            raise
        except Exception as exc:
            raise LocalRecognitionError(f"本地 OCR 运行失败：{exc}") from exc


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value)


class LocalGameParser:
    CORE_LABELS = {
        "card_count": [r"牌数", r"卡牌数", r"角色数", r"cards?", r"players?", r"seats?"],
        "evil_count": [r"恶徒总数", r"恶徒", r"evils?", r"baddies"],
        "minion_count": [r"走卒数", r"走卒", r"爪牙数", r"爪牙", r"minions?"],
        "demon_count": [r"恶魔数", r"恶魔", r"demons?"],
        "health": [r"生命值", r"生命", r"health", r"life", r"hp"],
    }

    def __init__(self, catalog: RoleCatalog | None = None) -> None:
        self.catalog = catalog or RoleCatalog.load_default()
        self._role_aliases: list[tuple[str, RoleDefinition]] = []
        for role in self.catalog.roles.values():
            for alias in [role.role_id, role.name_en, role.name_zh, *role.aliases]:
                normalized = _normalize(alias)
                if len(normalized) >= 2:
                    self._role_aliases.append((normalized, role))
        self._role_aliases.sort(key=lambda item: len(item[0]), reverse=True)

    def parse_village(self, document: OcrDocument) -> VillageSetupSuggestion:
        values = {
            name: self._find_labeled_int(document, labels)
            for name, labels in self.CORE_LABELS.items()
        }
        maximums = {}
        for name in ["evil_count", "minion_count", "demon_count"]:
            count_range = self._find_labeled_range(
                document, self.CORE_LABELS[name]
            )
            if count_range is not None:
                values[name], maximums[name] = count_range
            elif values[name] is not None:
                maximums[name] = values[name]
        if values["card_count"] is None:
            values["card_count"] = self._infer_card_count(document)
        if values["evil_count"] is None and all(
            values[name] is not None for name in ["minion_count", "demon_count"]
        ):
            values["evil_count"] = (
                values["minion_count"] + values["demon_count"]
            )
            maximums["evil_count"] = (
                maximums["minion_count"] + maximums["demon_count"]
            )

        required = ["card_count", "evil_count", "minion_count", "demon_count"]
        missing = [name for name in required if values[name] is None]
        warnings = []
        label_names = {
            "card_count": "牌数",
            "evil_count": "恶徒总数",
            "minion_count": "爪牙/走卒数",
            "demon_count": "恶魔数",
        }
        if missing:
            warnings.append(
                "未识别到" + "、".join(label_names[name] for name in missing) + "；请重新截取包含村庄统计的总览。"
            )

        roles = []
        for token in document.tokens:
            if self._is_stat_label(token.text):
                continue
            for role, match_score in self._match_roles(token.text):
                if match_score >= 0.76 and role.role_id not in roles:
                    roles.append(role.role_id)

        health = values["health"]
        if health is None:
            health = self._infer_health(document)
        if health is None:
            health = 10
            warnings.append("未识别生命值，暂按 10 填入；创建前请确认。")

        config = None
        if not missing:
            try:
                config = VillageConfig(
                    language=self._detect_language(document),
                    card_count=values["card_count"],
                    evil_count=values["evil_count"],
                    evil_count_max=maximums["evil_count"],
                    minion_count=values["minion_count"],
                    minion_count_max=maximums["minion_count"],
                    demon_count=values["demon_count"],
                    demon_count_max=maximums["demon_count"],
                    health=health,
                    deck_roles=roles,
                )
            except ValueError as exc:
                warnings.append(f"识别出的数量不符合标准村庄规则：{exc}")

        confidences = [token.confidence for token in document.tokens if token.text]
        return VillageSetupSuggestion(
            config=config,
            warnings=warnings,
            overall_confidence=(sum(confidences) / len(confidences)) if confidences else 0,
            recognition_engine="rapidocr-local",
            raw_text=[token.text for token in document.tokens if token.text],
        )

    def parse_state(self, document: OcrDocument, state: GameState) -> StatePatch:
        position_tokens = self._seat_position_tokens(
            document, state.config.card_count
        )
        best_by_position: dict[
            int, tuple[float, OcrToken, RoleDefinition]
        ] = {}
        warnings = []
        for role_token in document.tokens:
            matched = self._match_role(role_token.text)
            if not matched:
                continue
            role, match_score = matched
            score = role_token.confidence * match_score
            if score < 0.72:
                continue
            position = self._role_position(
                role_token, position_tokens, document.width, document.height
            )
            if position is None:
                continue
            previous = best_by_position.get(position)
            if previous is None or score > previous[0]:
                best_by_position[position] = (score, role_token, role)

        claims = self._claim_text_by_position(
            document, position_tokens, best_by_position
        )
        seats = []
        for position, (_, role_token, role) in sorted(best_by_position.items()):
            claim_text = claims.get(position)
            values = {"position": position, "visible_role": role.name_en}
            if claim_text:
                values["claim_text"] = claim_text
            status_text = _normalize(" ".join([claim_text or "", role_token.text]))
            if any(word in status_text for word in ["已翻开", "翻开", "revealed", "faceup"]):
                values["revealed"] = True
            if any(word in status_text for word in ["死亡", "已死", "dead"]):
                values["alive"] = False
            if any(word in status_text for word in ["腐化", "corrupted", "poisoned"]):
                values["corrupted"] = True
            seats.append(SeatState.model_validate(values))

        scores = [score for score, _, _ in best_by_position.values()]
        if not best_by_position:
            warnings.append("未能把任何角色名称与牌位配对；请确认截图是否包含已翻开的牌。")
        return StatePatch(
            seats=seats,
            warnings=warnings,
            overall_confidence=(sum(scores) / len(scores)) if scores else 0,
            recognition_engine="rapidocr-local",
            raw_text=[token.text for token in document.tokens if token.text],
        )

    def _find_labeled_int(self, document: OcrDocument, labels: list[str]) -> int | None:
        label_pattern = "(?:" + "|".join(labels) + ")"
        for token in document.tokens:
            match = re.search(
                r"(\d{1,2})\s*(?:个|名|只)\s*" + label_pattern,
                unicodedata.normalize("NFKC", token.text),
                re.IGNORECASE,
            )
            if match:
                return int(match.group(1))

        for token in document.tokens:
            progress = re.search(
                label_pattern + r"\s*[:：=]?\s*\d{1,2}\s*/\s*(\d{1,2})",
                unicodedata.normalize("NFKC", token.text),
                re.IGNORECASE,
            )
            if progress:
                return int(progress.group(1))

        for token in document.tokens:
            match = re.search(
                label_pattern + r"\s*[:：=]?\s*(\d{1,2})",
                unicodedata.normalize("NFKC", token.text),
                re.IGNORECASE,
            )
            if match:
                return int(match.group(1))

        label_tokens = [
            token
            for token in document.tokens
            if re.search(label_pattern, token.text, re.IGNORECASE)
        ]
        number_tokens = [
            (token, int(match.group(1)))
            for token in document.tokens
            if (match := re.fullmatch(r"\s*(\d{1,2})\s*", token.text))
        ]
        for label in label_tokens:
            candidates = sorted(
                number_tokens,
                key=lambda item: self._distance(label, item[0], document.width, document.height),
            )
            if candidates and self._distance(label, candidates[0][0], document.width, document.height) < 0.18:
                return candidates[0][1]
        return None

    def _find_labeled_range(
        self, document: OcrDocument, labels: list[str]
    ) -> tuple[int, int] | None:
        label_pattern = "(?:" + "|".join(labels) + ")"
        separator = r"(?:-|–|—|~|至|到)"
        patterns = [
            r"(\d{1,2})\s*" + separator + r"\s*(\d{1,2})\s*(?:个|名|只)?\s*" + label_pattern,
            label_pattern + r"\s*[:：=]?\s*\d{1,2}\s*/\s*(\d{1,2})\s*" + separator + r"\s*(\d{1,2})",
            label_pattern + r"\s*[:：=]?\s*(\d{1,2})\s*" + separator + r"\s*(\d{1,2})",
        ]
        for token in document.tokens:
            text = unicodedata.normalize("NFKC", token.text)
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    minimum, maximum = int(match.group(1)), int(match.group(2))
                    if minimum <= maximum:
                        return minimum, maximum
        return None

    @staticmethod
    def _infer_health(document: OcrDocument) -> int | None:
        candidates = []
        for token in document.tokens:
            match = re.fullmatch(r"\s*(\d{1,2})\s*", token.text)
            if not match:
                continue
            x = token.center[0] / max(document.width, 1)
            y = token.center[1] / max(document.height, 1)
            if x < 0.25 and y > 0.68:
                candidates.append((token.confidence, -abs(x - 0.10), int(match.group(1))))
        return max(candidates)[2] if candidates else None

    def _infer_card_count(self, document: OcrDocument) -> int | None:
        positions = set()
        for token in document.tokens:
            if not re.fullmatch(
                r"\s*(?:#|牌位|seat)\s*\d{1,2}\s*",
                token.text,
                re.IGNORECASE,
            ):
                continue
            for match in re.finditer(r"(?:#|牌位|seat)\s*(\d{1,2})", token.text, re.IGNORECASE):
                positions.add(int(match.group(1)))
        if len(positions) >= 3:
            maximum = max(positions)
            enough_labels = len(positions) >= max(3, maximum - 1)
            if enough_labels and maximum <= 20:
                return maximum
        return None

    def _match_role(self, text: str) -> tuple[RoleDefinition, float] | None:
        matches = self._match_roles(text)
        return matches[0] if matches else None

    def _match_roles(self, text: str) -> list[tuple[RoleDefinition, float]]:
        candidate = re.sub(r"(?:#|seat|牌位)\s*\d{1,2}", "", text, flags=re.IGNORECASE)
        normalized = _normalize(candidate)
        if len(normalized) < 2:
            return []
        exact = []
        seen = set()
        for alias, role in self._role_aliases:
            if alias in normalized and role.role_id not in seen:
                exact.append((role, 0.99))
                seen.add(role.role_id)
        if exact:
            return exact
        best = None
        for alias, role in self._role_aliases:
            ratio = SequenceMatcher(None, normalized, alias).ratio()
            if best is None or ratio > best[1]:
                best = (role, ratio)
        return [best] if best and best[1] >= 0.70 else []

    def _is_stat_label(self, text: str) -> bool:
        return any(
            re.search("(?:" + "|".join(labels) + ")", text, re.IGNORECASE)
            for labels in self.CORE_LABELS.values()
        )

    def _seat_position_tokens(
        self, document: OcrDocument, maximum: int
    ) -> list[tuple[OcrToken, int]]:
        result = []
        for token in document.tokens:
            if not re.fullmatch(
                r"\s*(?:#|牌位|seat)\s*\d{1,2}\s*",
                token.text,
                re.IGNORECASE,
            ):
                continue
            position = self._position_in_text(token.text, maximum)
            if position is not None:
                result.append((token, position))
        return result

    @staticmethod
    def _position_in_text(text: str, maximum: int) -> int | None:
        match = re.search(r"(?:#|牌位|seat)\s*(\d{1,2})", text, re.IGNORECASE)
        if not match:
            return None
        position = int(match.group(1))
        return position if 1 <= position <= maximum else None

    def _role_position(
        self,
        role_token: OcrToken,
        positions: list[tuple[OcrToken, int]],
        width: int,
        height: int,
    ) -> int | None:
        candidates = []
        for position_token, position in positions:
            horizontal = abs(
                role_token.center[0] - position_token.center[0]
            ) / max(width, 1)
            vertical = (
                role_token.center[1] - position_token.center[1]
            ) / max(height, 1)
            if horizontal <= 0.08 and 0.02 <= vertical <= 0.24:
                cost = horizontal * 2 + abs(vertical - 0.145)
                candidates.append((cost, position))
        return min(candidates)[1] if candidates else None

    def _claim_text_by_position(
        self,
        document: OcrDocument,
        position_tokens: list[tuple[OcrToken, int]],
        roles_by_position: dict[int, tuple[float, OcrToken, RoleDefinition]],
    ) -> dict[int, str]:
        position_by_number = {
            position: token for token, position in position_tokens
        }
        card_centers = {}
        role_label_objects = set()
        for position, (_, role_token, _) in roles_by_position.items():
            position_token = position_by_number.get(position)
            if position_token is None:
                continue
            role_label_objects.add(id(role_token))
            card_centers[position] = (
                (position_token.center[0] + role_token.center[0]) / 2,
                (position_token.center[1] + role_token.center[1]) / 2,
            )

        position_objects = {id(token) for token, _ in position_tokens}
        claim_tokens: dict[int, list[OcrToken]] = {}
        for token in document.tokens:
            if id(token) in position_objects or id(token) in role_label_objects:
                continue
            normalized = _normalize(token.text)
            if not normalized:
                continue
            if re.fullmatch(r"[a-z0-9]+", normalized) and len(normalized) <= 2:
                continue
            if not card_centers:
                continue
            nearest_position, distance = min(
                (
                    (
                        position,
                        math.hypot(
                            token.center[0] - center[0],
                            token.center[1] - center[1],
                        ),
                    )
                    for position, center in card_centers.items()
                ),
                key=lambda item: item[1],
            )
            if distance / max(min(document.width, document.height), 1) <= 0.18:
                claim_tokens.setdefault(nearest_position, []).append(token)

        return {
            position: self._join_claim_tokens(tokens)
            for position, tokens in claim_tokens.items()
            if tokens
        }

    @staticmethod
    def _join_claim_tokens(tokens: list[OcrToken]) -> str:
        result = ""
        for token in sorted(tokens, key=lambda item: (item.top, item.left)):
            text = token.text.strip()
            if result and re.fullmatch(r"[\u4e00-\u9fff]", text):
                result += text
            else:
                result += (" " if result else "") + text
        return result

    @staticmethod
    def _distance(first: OcrToken, second: OcrToken, width: int, height: int) -> float:
        dx = (first.center[0] - second.center[0]) / max(width, 1)
        dy = (first.center[1] - second.center[1]) / max(height, 1)
        return math.hypot(dx, dy)

    @staticmethod
    def _detect_language(document: OcrDocument) -> str:
        joined = "".join(token.text for token in document.tokens)
        return "zh-Hans" if re.search(r"[\u4e00-\u9fff]", joined) else "en"


class LocalVisionService:
    def __init__(
        self,
        engine: OcrEngine | None = None,
        parser: LocalGameParser | None = None,
    ) -> None:
        self.engine = engine or RapidOCREngine()
        self.parser = parser or LocalGameParser()

    def parse_village(self, png_bytes: bytes) -> VillageSetupSuggestion:
        return self.parser.parse_village(self.engine.recognize(png_bytes))

    def parse_capture(self, png_bytes: bytes, state: GameState) -> StatePatch:
        return self.parser.parse_state(self.engine.recognize(png_bytes), state)
