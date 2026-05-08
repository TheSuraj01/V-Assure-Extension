import copy
import json
import math
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Optional, Set

from config.config_loader import get_config
from utils import setup_logger

logger = setup_logger(__name__)

config = get_config()


class EnhancedRAGEngine:
    def __init__(self):

        self.entries: List[Dict[str, Any]] = []

        self.tfidf_index: Dict[
            str,
            Dict[int, float],
        ] = {}

        self.bm25_index: Dict[
            str,
            Dict[int, float],
        ] = {}

        self.action_clusters: DefaultDict[
            str,
            List[int],
        ] = defaultdict(list)

        self.label_index: DefaultDict[
            str,
            List[int],
        ] = defaultdict(list)

        self._loaded_files: List[str] = []

        rag_config = config.get("rag", {})

        self.k1 = rag_config.get(
            "bm25_k1",
            1.5,
        )

        self.b = rag_config.get(
            "bm25_b",
            0.75,
        )

        self.avg_doc_length = 0.0

        self.workflow_patterns = (
            self._init_workflow_patterns()
        )

    # ─────────────────────────────────────────────────────
    # Workflow Patterns
    # ─────────────────────────────────────────────────────

    def _init_workflow_patterns(
        self,
    ) -> Dict[str, List[str]]:

        return {
            "navigation": [
                "navigate",
                "go to",
                "click on the",
                "select tab",
                "open",
            ],
            "data_entry": [
                "enter",
                "type",
                "input",
                "fill in",
                "provide",
            ],
            "selection": [
                "select",
                "choose",
                "pick",
                "from the dropdown",
                "from dropdown list",
            ],
            "verification": [
                "verify",
                "check",
                "confirm",
                "ensure",
                "validate",
            ],
            "action": [
                "click",
                "press",
                "tap",
                "hit",
            ],
            "save": [
                "save",
                "submit",
                "apply",
                "confirm changes",
            ],
            "cancel": [
                "cancel",
                "discard",
                "close",
                "dismiss",
            ],
        }

    # ─────────────────────────────────────────────────────
    # Loaders
    # ─────────────────────────────────────────────────────

    def load_file(
        self,
        path: str,
    ) -> int:

        try:
            with open(
                path,
                "r",
                encoding="utf-8",
            ) as file:
                data = json.load(file)

            if not isinstance(data, list):
                logger.warning(
                    "Skipping invalid KB file: %s",
                    path,
                )
                return 0

            before_count = len(self.entries)

            for entry in data:
                if self._is_valid_entry(entry):
                    self.entries.append(entry)

            added_count = (
                len(self.entries)
                - before_count
            )

            self._loaded_files.append(path)

            logger.info(
                "Loaded %s entries from %s",
                added_count,
                Path(path).name,
            )

            return added_count

        except Exception as exc:
            logger.exception(
                "Failed loading KB file: %s",
                path,
            )
            return 0

    def load_directory(
        self,
        directory: str,
        pattern: str = "*.json",
    ) -> int:

        total = 0

        for path in sorted(
            Path(directory).glob(pattern)
        ):
            total += self.load_file(
                str(path)
            )

        return total

    def load_entries_direct(
        self,
        entries: List[Dict[str, Any]],
    ) -> int:

        before_count = len(self.entries)

        for entry in entries:
            if self._is_valid_entry(entry):
                self.entries.append(entry)

        return len(self.entries) - before_count

    # ─────────────────────────────────────────────────────
    # Validation
    # ─────────────────────────────────────────────────────

    def _is_valid_entry(
        self,
        entry: Dict[str, Any],
    ) -> bool:

        return (
            isinstance(entry, dict)
            and "name" in entry
            and "input" in entry
            and "output" in entry
            and isinstance(
                entry["input"],
                dict,
            )
        )

    # ─────────────────────────────────────────────────────
    # Index Builder
    # ─────────────────────────────────────────────────────

    def build_index(self) -> None:

        self.tfidf_index.clear()
        self.bm25_index.clear()

        self.action_clusters.clear()
        self.label_index.clear()

        if not self.entries:
            logger.warning(
                "No entries available to index"
            )
            return

        document_term_counts: List[
            Dict[str, int]
        ] = []

        document_lengths: List[int] = []

        for index, entry in enumerate(
            self.entries
        ):

            terms = self._extract_terms(
                entry
            )

            term_counts = Counter(terms)

            document_term_counts.append(
                term_counts
            )

            document_lengths.append(
                len(terms)
            )

            action = (
                entry.get("input", {})
                .get("action", "")
                .lower()
            )

            if action:
                self.action_clusters[
                    action
                ].append(index)

            label = (
                entry.get("input", {})
                .get("label", "")
                .lower()
            )

            normalized_label = (
                self._normalize_label(
                    label
                )
            )

            if normalized_label:
                self.label_index[
                    normalized_label
                ].append(index)

        total_documents = len(
            self.entries
        )

        self.avg_doc_length = (
            sum(document_lengths)
            / total_documents
        )

        document_frequency: Dict[
            str,
            int,
        ] = {}

        for counts in document_term_counts:
            for term in counts:
                document_frequency[term] = (
                    document_frequency.get(
                        term,
                        0,
                    )
                    + 1
                )

        for (
            doc_index,
            term_counts,
        ) in enumerate(document_term_counts):

            doc_length = document_lengths[
                doc_index
            ]

            total_terms = (
                sum(term_counts.values())
                or 1
            )

            for (
                term,
                count,
            ) in term_counts.items():

                tf = count / total_terms

                idf = (
                    math.log(
                        (
                            total_documents
                            + 1
                        )
                        / (
                            document_frequency[
                                term
                            ]
                            + 1
                        )
                    )
                    + 1
                )

                tfidf_score = tf * idf

                self.tfidf_index.setdefault(
                    term,
                    {},
                )[doc_index] = tfidf_score

                idf_bm25 = math.log(
                    (
                        total_documents
                        - document_frequency[
                            term
                        ]
                        + 0.5
                    )
                    / (
                        document_frequency[
                            term
                        ]
                        + 0.5
                    )
                    + 1
                )

                tf_bm25 = (
                    count
                    * (self.k1 + 1)
                ) / (
                    count
                    + self.k1
                    * (
                        1
                        - self.b
                        + self.b
                        * (
                            doc_length
                            / self.avg_doc_length
                        )
                    )
                )

                bm25_score = (
                    idf_bm25 * tf_bm25
                )

                self.bm25_index.setdefault(
                    term,
                    {},
                )[doc_index] = bm25_score

        logger.info(
            "RAG index built successfully | entries=%s",
            total_documents,
        )

    # ─────────────────────────────────────────────────────
    # Retrieval
    # ─────────────────────────────────────────────────────

    def retrieve(
        self,
        action: str,
        label: str,
        value: str = "",
        top_k: int = 5,
        diversity_weight: float = 0.3,
    ) -> List[str]:

        if not self.entries:
            return []

        query_terms = self._tokenize(
            f"{action} {label} {value}"
        )

        scores: Dict[int, float] = {}

        self._apply_bm25_scores(
            query_terms,
            scores,
        )

        self._apply_tfidf_scores(
            query_terms,
            scores,
        )

        self._apply_action_boost(
            action,
            scores,
        )

        self._apply_label_similarity(
            label,
            scores,
        )

        self._apply_value_similarity(
            value,
            scores,
        )

        self._apply_workflow_boost(
            action,
            label,
            value,
            scores,
        )

        ranked_results = sorted(
            scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )

        return self._apply_diversity_filter(
            ranked_results,
            top_k,
            diversity_weight,
        )

    # ─────────────────────────────────────────────────────
    # Retrieval Helpers
    # ─────────────────────────────────────────────────────

    def _apply_bm25_scores(
        self,
        query_terms: List[str],
        scores: Dict[int, float],
    ) -> None:

        for term in query_terms:
            if term not in self.bm25_index:
                continue

            for (
                doc_index,
                bm25_score,
            ) in self.bm25_index[
                term
            ].items():

                scores[doc_index] = (
                    scores.get(
                        doc_index,
                        0.0,
                    )
                    + bm25_score * 2.0
                )

    def _apply_tfidf_scores(
        self,
        query_terms: List[str],
        scores: Dict[int, float],
    ) -> None:

        for term in query_terms:
            if term not in self.tfidf_index:
                continue

            for (
                doc_index,
                tfidf_score,
            ) in self.tfidf_index[
                term
            ].items():

                scores[doc_index] = (
                    scores.get(
                        doc_index,
                        0.0,
                    )
                    + tfidf_score
                )

    def _apply_action_boost(
        self,
        action: str,
        scores: Dict[int, float],
    ) -> None:

        action_lower = action.lower()

        if (
            action_lower
            not in self.action_clusters
        ):
            return

        for doc_index in self.action_clusters[
            action_lower
        ]:
            scores[doc_index] = (
                scores.get(
                    doc_index,
                    0.0,
                )
                + 5.0
            )

    def _apply_label_similarity(
        self,
        label: str,
        scores: Dict[int, float],
    ) -> None:

        label_lower = label.lower()

        normalized_label = (
            self._normalize_label(
                label_lower
            )
        )

        label_words = set(
            label_lower.split()
        )

        for (
            index,
            entry,
        ) in enumerate(self.entries):

            entry_label = (
                entry.get("input", {})
                .get("label", "")
                .lower()
            )

            entry_normalized = (
                self._normalize_label(
                    entry_label
                )
            )

            if (
                normalized_label
                == entry_normalized
            ):
                scores[index] = (
                    scores.get(index, 0.0)
                    + 10.0
                )

            elif (
                normalized_label
                and entry_normalized
            ):

                similarity = (
                    self._fuzzy_similarity(
                        normalized_label,
                        entry_normalized,
                    )
                )

                if similarity > 0.7:
                    scores[index] = (
                        scores.get(
                            index,
                            0.0,
                        )
                        + similarity * 6.0
                    )

            if (
                label_lower
                and entry_label
            ):
                if (
                    label_lower
                    in entry_label
                    or entry_label
                    in label_lower
                ):
                    scores[index] = (
                        scores.get(
                            index,
                            0.0,
                        )
                        + 4.0
                    )

            entry_words = set(
                entry_label.split()
            )

            overlap = len(
                label_words
                & entry_words
            )

            if overlap > 0:
                overlap_ratio = overlap / max(
                    len(label_words),
                    len(entry_words),
                )

                scores[index] = (
                    scores.get(
                        index,
                        0.0,
                    )
                    + overlap_ratio * 3.0
                )

    def _apply_value_similarity(
        self,
        value: str,
        scores: Dict[int, float],
    ) -> None:

        if not value:
            return

        value_lower = value.lower()

        for (
            index,
            entry,
        ) in enumerate(self.entries):

            entry_value = (
                entry.get("input", {})
                .get("value")
                or entry.get("input", {})
                .get("selectedText")
                or ""
            ).lower()

            if not entry_value:
                continue

            if value_lower == entry_value:
                scores[index] = (
                    scores.get(
                        index,
                        0.0,
                    )
                    + 3.0
                )

            elif (
                value_lower
                in entry_value
                or entry_value
                in value_lower
            ):
                scores[index] = (
                    scores.get(
                        index,
                        0.0,
                    )
                    + 1.5
                )

    def _apply_workflow_boost(
        self,
        action: str,
        label: str,
        value: str,
        scores: Dict[int, float],
    ) -> None:

        boosts = (
            self._get_workflow_boost(
                action,
                label,
                value,
            )
        )

        for index in scores:
            output = (
                self.entries[index]
                .get("output", "")
                .lower()
            )

            for (
                workflow_type,
                boost_score,
            ) in boosts.items():

                patterns = (
                    self.workflow_patterns.get(
                        workflow_type,
                        [],
                    )
                )

                if any(
                    keyword in output
                    for keyword in patterns
                ):
                    scores[index] = (
                        scores.get(
                            index,
                            0.0,
                        )
                        + boost_score
                    )

    def _apply_diversity_filter(
        self,
        ranked_results,
        top_k: int,
        diversity_weight: float,
    ) -> List[str]:

        results: List[str] = []

        seen_outputs: Set[str] = set()
        seen_patterns: Set[str] = set()

        for (
            doc_index,
            _score,
        ) in ranked_results[: top_k * 3]:

            output = (
                self.entries[doc_index]
                .get("output", "")
                .strip()
            )

            if (
                not output
                or output in seen_outputs
            ):
                continue

            pattern = " ".join(
                output.lower().split()[:3]
            )

            if (
                diversity_weight > 0
                and pattern in seen_patterns
            ):
                continue

            seen_outputs.add(output)
            seen_patterns.add(pattern)

            results.append(output)

            if len(results) >= top_k:
                break

        return results

    # ─────────────────────────────────────────────────────
    # NLP Helpers
    # ─────────────────────────────────────────────────────

    def _normalize_label(
        self,
        label: str,
    ) -> str:

        label = re.sub(
            r"\s+(button|field|input|dropdown|menu|icon|tab|link)$",
            "",
            label,
            flags=re.IGNORECASE,
        )

        label = re.sub(
            r"[^\w\s]",
            "",
            label,
        )

        label = " ".join(
            label.split()
        )

        return label.lower().strip()

    def _fuzzy_similarity(
        self,
        str1: str,
        str2: str,
    ) -> float:

        return SequenceMatcher(
            None,
            str1,
            str2,
        ).ratio()

    def _get_workflow_boost(
        self,
        action: str,
        label: str,
        value: str,
    ) -> Dict[str, float]:

        boosts: Dict[str, float] = {}

        action_lower = action.lower()
        label_lower = label.lower()

        if action_lower == "click":

            if any(
                keyword in label_lower
                for keyword in [
                    "save",
                    "submit",
                    "apply",
                ]
            ):
                boosts["save"] = 2.0

            elif any(
                keyword in label_lower
                for keyword in [
                    "cancel",
                    "close",
                    "dismiss",
                ]
            ):
                boosts["cancel"] = 2.0

            elif any(
                keyword in label_lower
                for keyword in [
                    "tab",
                    "menu",
                    "nav",
                ]
            ):
                boosts["navigation"] = 2.5

            else:
                boosts["action"] = 1.0

        elif action_lower in [
            "enter",
            "input",
            "type",
        ]:
            boosts["data_entry"] = 2.0

        elif action_lower == "select":
            boosts["selection"] = 2.0

        elif action_lower == "verify":
            boosts["verification"] = 2.0

        return boosts

    def _extract_terms(
        self,
        entry: Dict[str, Any],
    ) -> List[str]:

        parts = [
            entry.get("name", ""),
            entry.get("output", ""),
            entry.get("input", {}).get(
                "action",
                "",
            ),
            entry.get("input", {}).get(
                "label",
                "",
            ),
            entry.get("input", {}).get(
                "value",
                "",
            )
            or "",
            entry.get("input", {}).get(
                "selectedText",
                "",
            )
            or "",
            entry.get("input", {}).get(
                "placeholder",
                "",
            )
            or "",
            entry.get("input", {}).get(
                "ariaLabel",
                "",
            )
            or "",
        ]

        return self._tokenize(
            " ".join(
                str(part)
                for part in parts
                if part
            )
        )

    def _tokenize(
        self,
        text: str,
    ) -> List[str]:

        stopwords = set(
            config.get(
                "rag.stopwords",
                [
                    "the",
                    "a",
                    "an",
                    "in",
                    "on",
                    "at",
                    "to",
                    "for",
                    "of",
                    "and",
                    "or",
                    "is",
                    "it",
                    "this",
                    "that",
                    "with",
                    "from",
                    "by",
                    "be",
                    "as",
                    "veeva",
                    "vault",
                ],
            )
        )

        text = text.lower()

        text = re.sub(
            r"[^a-z0-9\s_\-]",
            " ",
            text,
        )

        tokens = text.split()

        return [
            token
            for token in tokens
            if (
                token
                and token not in stopwords
                and len(token) > 1
            )
        ]

    # ─────────────────────────────────────────────────────
    # Utility Methods
    # ─────────────────────────────────────────────────────

    def clone(self):

        return copy.deepcopy(self)

    def clear(self) -> None:

        self.entries.clear()

        self.tfidf_index.clear()
        self.bm25_index.clear()

        self.action_clusters.clear()
        self.label_index.clear()

        self._loaded_files.clear()

        self.avg_doc_length = 0.0

    # ─────────────────────────────────────────────────────
    # Properties
    # ─────────────────────────────────────────────────────

    @property
    def loaded_files(
        self,
    ) -> List[str]:

        return self._loaded_files

    @property
    def total_entries(
        self,
    ) -> int:

        return len(self.entries)

    # ─────────────────────────────────────────────────────
    # Stats
    # ─────────────────────────────────────────────────────

    def get_stats(
        self,
    ) -> Dict[str, Any]:

        return {
            "total_entries": len(
                self.entries
            ),
            "loaded_files": len(
                self._loaded_files
            ),
            "action_types": len(
                self.action_clusters
            ),
            "unique_labels": len(
                self.label_index
            ),
            "avg_doc_length": round(
                self.avg_doc_length,
                2,
            ),
            "index_terms": len(
                self.bm25_index
            ),
        }