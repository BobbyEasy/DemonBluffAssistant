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
        if values["card_count"] is None:
            values["card_count"] = self._infer_card_count(document)
        if values["evil_count"] is None and all(
            values[name] is not None for name in ["minion_count", "demon_count"]
        ):
            values["evil_count"] = (
                values["minion_count"] + values["demon_count"]
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
                    minion_count=values["minion_count"],
                    demon_count=values["demon_count"],
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
        position_tokens = self._position_tokens(document, state.config.card_count)
        best_by_position: dict[int, tuple[float, SeatState]] = {}
        warnings = []
        for role_token in document.tokens:
            matched = self._match_role(role_token.text)
            if not matched:
                continue
            role, match_score = matched
            score = role_token.confidence * match_score
            if score < 0.72:
                continue
            position = self._position_in_text(role_token.text, state.config.card_count)
            if position is None:
                position = self._nearest_position(
                    role_token, position_tokens, document.width, document.height
                )
            if position is None:
                warnings.append(f"识别到角色“{role.name_zh}”，但无法确定牌位。")
                continue

            nearby = self._nearby_claim_text(role_token, document, position_tokens)
            values = {"position": position, "visible_role": role.name_en}
            if nearby:
                values["claim_text"] = " ".join(nearby)
            status_text = _normalize(" ".join(nearby + [role_token.text]))
            if any(word in status_text for word in ["已翻开", "翻开", "revealed", "faceup"]):
                values["revealed"] = True
            if any(word in status_text for word in ["死亡", "已死", "dead"]):
                values["alive"] = False
            if any(word in status_text for word in ["腐化", "corrupted", "poisoned"]):
                values["corrupted"] = True
            seat = SeatState.model_validate(values)
            previous = best_by_position.get(position)
            if previous is None or score > previous[0]:
                best_by_position[position] = (score, seat)

        scores = [score for score, _ in best_by_position.values()]
        if not best_by_position:
            warnings.append("未能把任何角色名称与牌位配对；请确认截图是否包含已翻开的牌。")
        return StatePatch(
            seats=[item[1] for item in sorted(best_by_position.values(), key=lambda item: item[1].position)],
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
            for match in re.finditer(r"(?:#|牌位|seat)\s*(\d{1,2})", token.text, re.IGNORECASE):
                positions.add(int(match.group(1)))
        if len(positions) >= 3:
            maximum = max(positions)
            if positions.issuperset(range(1, maximum + 1)) and maximum <= 20:
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

    def _position_tokens(self, document: OcrDocument, maximum: int) -> list[tuple[OcrToken, int]]:
        result = []
        for token in document.tokens:
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

    def _nearest_position(
        self,
        role_token: OcrToken,
        positions: list[tuple[OcrToken, int]],
        width: int,
        height: int,
    ) -> int | None:
        if not positions:
            return None
        nearest = min(
            positions,
            key=lambda item: self._distance(role_token, item[0], width, height),
        )
        return nearest[1] if self._distance(role_token, nearest[0], width, height) < 0.22 else None

    def _nearby_claim_text(
        self,
        role_token: OcrToken,
        document: OcrDocument,
        position_tokens: list[tuple[OcrToken, int]],
    ) -> list[str]:
        position_objects = {id(token) for token, _ in position_tokens}
        result = []
        for token in document.tokens:
            if token is role_token or id(token) in position_objects:
                continue
            if self._match_role(token.text):
                continue
            if len(_normalize(token.text)) < 3:
                continue
            horizontal = abs(token.center[0] - role_token.center[0]) / max(document.width, 1)
            vertical = abs(token.center[1] - role_token.center[1]) / max(document.height, 1)
            if horizontal < 0.20 and vertical < 0.16:
                result.append(token.text)
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
