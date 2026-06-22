import copy
import json
import math
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Optional, Set, Tuple

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

        # NEW: pre-built index for O(1) value lookups
        self.value_index: DefaultDict[
            str,
            List[int],
        ] = defaultdict(list)

        # NEW: normalized label → list of (doc_index, original_label) pairs
        self.normalized_label_index: DefaultDict[
            str,
            List[Tuple[int, str]],
        ] = defaultdict(list)

        self._loaded_files: List[str] = []

        rag_config = config.get("rag", {})

        self.k1 = rag_config.get("bm25_k1", 1.5)
        self.b = rag_config.get("bm25_b", 0.75)

        # NEW: configurable boost scores (BUG 3 fix — was hardcoded 5.0)
        self._action_boost_score: float = rag_config.get(
            "action_boost_score", 2.5
        )
        self._bm25_weight: float = rag_config.get("bm25_weight", 2.0)
        self._tfidf_weight: float = rag_config.get("tfidf_weight", 1.0)

        self.avg_doc_length = 0.0

        # NEW: cache stopwords at construction time (BUG 4 fix — was rebuilt per call)
        self._stopwords: Set[str] = set(
            config.get(
                "rag.stopwords",
                [
                    "the", "a", "an", "in", "on", "at", "to",
                    "for", "of", "and", "or", "is", "it", "this",
                    "that", "with", "from", "by", "be", "as",
                    "veeva", "vault",
                ],
            )
        )

        self.workflow_patterns = self._init_workflow_patterns()

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
        self.value_index.clear()                  # NEW
        self.normalized_label_index.clear()       # NEW

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
            terms = self._extract_terms(entry)
            term_counts = Counter(terms)
            document_term_counts.append(term_counts)
            document_lengths.append(len(terms))

            # ── Action cluster ─────────────────────────────────
            action = (
                entry.get("input", {})
                .get("action", "")
                .lower()
            )
            if action:
                self.action_clusters[action].append(index)

            # ── Label index ────────────────────────────────────
            label = (
                entry.get("input", {})
                .get("label", "")
                .lower()
            )
            normalized_label = self._normalize_label(label)
            if normalized_label:
                self.label_index[normalized_label].append(index)

            # NEW: also store (index, raw_label) in normalized_label_index
            # so we can do targeted fuzzy matching without a full scan
            if normalized_label:
                self.normalized_label_index[normalized_label].append(
                    (index, label)
                )

            # NEW: value index (BUG 9 fix — was O(N) scan per query)
            value = (
                entry.get("input", {}).get("value")
                or entry.get("input", {}).get("selectedText")
                or ""
            ).lower().strip()
            if value:
                self.value_index[value].append(index)
                # Also index partial value words for substring matching
                for word in value.split():
                    if len(word) > 2:
                        self.value_index[word].append(index)

        total_documents = len(self.entries)

        self.avg_doc_length = (
            sum(document_lengths) / total_documents
        )

        document_frequency: Dict[str, int] = {}

        for counts in document_term_counts:
            for term in counts:
                document_frequency[term] = (
                    document_frequency.get(term, 0) + 1
                )

        for (
            doc_index,
            term_counts,
        ) in enumerate(document_term_counts):

            doc_length = document_lengths[doc_index]
            total_terms = sum(term_counts.values()) or 1

            for (
                term,
                count,
            ) in term_counts.items():

                tf = count / total_terms

                idf = (
                    math.log(
                        (total_documents + 1)
                        / (document_frequency[term] + 1)
                    )
                    + 1
                )

                tfidf_score = tf * idf

                self.tfidf_index.setdefault(
                    term, {}
                )[doc_index] = tfidf_score

                idf_bm25 = math.log(
                    (
                        total_documents
                        - document_frequency[term]
                        + 0.5
                    )
                    / (document_frequency[term] + 0.5)
                    + 1
                )

                tf_bm25 = (
                    count * (self.k1 + 1)
                ) / (
                    count
                    + self.k1
                    * (
                        1
                        - self.b
                        + self.b
                        * (doc_length / self.avg_doc_length)
                    )
                )

                bm25_score = idf_bm25 * tf_bm25

                self.bm25_index.setdefault(
                    term, {}
                )[doc_index] = bm25_score

        logger.info(
            "RAG index built successfully | entries=%s | unique_labels=%s | value_keys=%s",
            total_documents,
            len(self.normalized_label_index),
            len(self.value_index),
        )

    # ─────────────────────────────────────────────────────
    # Retrieval — Public API
    # ─────────────────────────────────────────────────────

    def retrieve(
        self,
        action: str,
        label: str,
        value: str = "",
        top_k: int = 5,
        diversity_weight: float = 0.3,
    ) -> List[str]:
        """
        Returns a list of output strings (backward-compatible).
        Internally delegates to _score_and_rank.
        """
        ranked = self._score_and_rank(
            action=action,
            label=label,
            value=value,
        )

        results = self._apply_diversity_filter(
            ranked,
            top_k,
            diversity_weight,
            return_entries=False,
        )

        logger.debug(
            "[RAG] retrieve() → %d/%d chunks for label=%r",
            len(results),
            top_k,
            label,
        )

        for i, chunk in enumerate(results, 1):
            logger.debug(
                "[RAG] Chunk %d: %r",
                i,
                chunk[:120],
            )

        return results  # type: ignore[return-value]

    def retrieve_with_context(
        self,
        action: str,
        label: str,
        value: str = "",
        top_k: int = 5,
        diversity_weight: float = 0.3,
    ) -> List[Dict[str, str]]:
        """
        NEW public method (BUG 6 fix).

        Returns a list of dicts with full input→output context so the
        LLM prompt can show the *mapping pattern*, not just raw output strings.

        Each dict has the shape:
            {
                "action":  str,
                "label":   str,
                "value":   str,   # may be empty
                "output":  str,
            }
        """
        if not self.entries:
            return []

        ranked = self._score_and_rank(
            action=action,
            label=label,
            value=value,
        )

        context_items = self._apply_diversity_filter(
            ranked,
            top_k,
            diversity_weight,
            return_entries=True,
        )

        logger.debug(
            "[RAG] retrieve_with_context() → %d/%d for label=%r",
            len(context_items),
            top_k,
            label,
        )

        return context_items  # type: ignore[return-value]

    # ─────────────────────────────────────────────────────
    # Scoring Core
    # ─────────────────────────────────────────────────────

    def _score_and_rank(
        self,
        action: str,
        label: str,
        value: str,
    ) -> List[Tuple[int, float]]:
        """
        Central scoring pipeline.  Returns a sorted list of
        (doc_index, score) tuples, highest score first.
        """
        if not self.entries:
            logger.debug(
                "[RAG] _score_and_rank() called but no entries loaded"
            )
            return []

        query_terms = self._tokenize(
            f"{action} {label} {value}"
        )

        logger.debug(
            "[RAG] Query | action=%r label=%r value=%r | tokens=%s",
            action,
            label,
            value,
            query_terms,
        )

        scores: Dict[int, float] = {}

        self._apply_bm25_scores(query_terms, scores)
        self._apply_tfidf_scores(query_terms, scores)
        self._apply_action_boost(action, scores)
        self._apply_label_similarity(label, scores)
        self._apply_value_similarity(value, scores)
        self._apply_workflow_boost(action, label, value, scores)

        ranked = sorted(
            scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )

        # Debug: log top-10
        if logger.isEnabledFor(10):
            for rank, (doc_idx, doc_score) in enumerate(ranked[:10], 1):
                entry = self.entries[doc_idx]
                logger.debug(
                    "[RAG] Rank %d | score=%.3f | action=%r label=%r | output=%r",
                    rank,
                    doc_score,
                    entry.get("input", {}).get("action", ""),
                    entry.get("input", {}).get("label", ""),
                    entry.get("output", "")[:80],
                )

        return ranked

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
            ) in self.bm25_index[term].items():

                scores[doc_index] = (
                    scores.get(doc_index, 0.0)
                    + bm25_score * self._bm25_weight
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
            ) in self.tfidf_index[term].items():

                scores[doc_index] = (
                    scores.get(doc_index, 0.0)
                    + tfidf_score * self._tfidf_weight
                )

    def _apply_action_boost(
        self,
        action: str,
        scores: Dict[int, float],
    ) -> None:
        """
        BUG 3 fix: was a flat +5.0 hardcoded constant.
        Now reads from config and uses a graduated boost:
          - exact action match → action_boost_score (2.5)
          - no match          → no boost
        """
        action_lower = action.lower()

        if action_lower not in self.action_clusters:
            return

        boost = self._action_boost_score

        for doc_index in self.action_clusters[action_lower]:
            scores[doc_index] = (
                scores.get(doc_index, 0.0) + boost
            )

    def _apply_label_similarity(
        self,
        label: str,
        scores: Dict[int, float],
    ) -> None:
        """
        BUG 1 + BUG 2 fix: was an O(N) scan that ignored self.label_index.

        New strategy (fast + accurate):
          1. Exact normalized match via self.normalized_label_index → +10.0
          2. Substring containment via index keys → +4.0 proportional
          3. Fuzzy SequenceMatcher ONLY on index candidates, not all entries
          4. Word-overlap fallback — O(unique_labels) not O(N entries)
        """
        label_lower = label.lower()
        normalized_query = self._normalize_label(label_lower)
        query_words = set(label_lower.split())

        # ── Step 1: exact normalized match (O(1)) ──────────────────
        if normalized_query in self.normalized_label_index:
            for doc_index, _ in self.normalized_label_index[normalized_query]:
                scores[doc_index] = (
                    scores.get(doc_index, 0.0) + 10.0
                )

        # ── Step 2: substring + fuzzy across index keys (O(unique_labels)) ──
        for entry_normalized, entry_pairs in self.normalized_label_index.items():

            if entry_normalized == normalized_query:
                continue  # already handled above

            # substring check
            if (
                normalized_query
                and entry_normalized
                and (
                    normalized_query in entry_normalized
                    or entry_normalized in normalized_query
                )
            ):
                for doc_index, _ in entry_pairs:
                    scores[doc_index] = (
                        scores.get(doc_index, 0.0) + 4.0
                    )
                continue

            # fuzzy similarity (only run if both have content)
            if normalized_query and entry_normalized:
                similarity = self._fuzzy_similarity(
                    normalized_query, entry_normalized
                )
                if similarity > 0.7:
                    for doc_index, _ in entry_pairs:
                        scores[doc_index] = (
                            scores.get(doc_index, 0.0)
                            + similarity * 6.0
                        )
                    continue

            # word overlap
            entry_words = set(entry_normalized.split())
            overlap = len(query_words & entry_words)
            if overlap > 0:
                overlap_ratio = overlap / max(
                    len(query_words), len(entry_words), 1
                )
                for doc_index, _ in entry_pairs:
                    scores[doc_index] = (
                        scores.get(doc_index, 0.0)
                        + overlap_ratio * 3.0
                    )

    def _apply_value_similarity(
        self,
        value: str,
        scores: Dict[int, float],
    ) -> None:
        """
        BUG 9 fix: was an O(N) scan per query.
        Now uses self.value_index for O(1) exact lookup + targeted partial.
        """
        if not value:
            return

        value_lower = value.lower().strip()

        # ── Exact match via index (O(1)) ─────────────────────────
        if value_lower in self.value_index:
            for doc_index in self.value_index[value_lower]:
                scores[doc_index] = (
                    scores.get(doc_index, 0.0) + 3.0
                )

        # ── Partial word match via index (O(words)) ───────────────
        for word in value_lower.split():
            if len(word) <= 2:
                continue
            if word in self.value_index:
                for doc_index in self.value_index[word]:
                    # avoid double-counting exact matches
                    current = scores.get(doc_index, 0.0)
                    # only add partial boost if we haven't already given exact boost
                    scores[doc_index] = current + 1.0

    def _apply_workflow_boost(
        self,
        action: str,
        label: str,
        value: str,
        scores: Dict[int, float],
    ) -> None:
        """
        BUG fix: was iterating ALL scored docs with string matching (O(N×patterns)).
        Now caps the candidate pool to top_k * 3 before applying pattern checks.
        """
        boosts = self._get_workflow_boost(action, label, value)

        if not boosts:
            return

        # Only check top candidates, not all scored docs
        top_candidates = sorted(
            scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )[: len(self.entries) // 3 + 1]  # cap at ~33% of corpus

        for (
            index,
            _,
        ) in top_candidates:
            output = (
                self.entries[index]
                .get("output", "")
                .lower()
            )

            for (
                workflow_type,
                boost_score,
            ) in boosts.items():

                patterns = self.workflow_patterns.get(
                    workflow_type, []
                )

                if any(
                    keyword in output
                    for keyword in patterns
                ):
                    scores[index] = (
                        scores.get(index, 0.0) + boost_score
                    )

    def _apply_diversity_filter(
        self,
        ranked_results: List[Tuple[int, float]],
        top_k: int,
        diversity_weight: float,
        return_entries: bool = False,
    ):
        """
        BUG 5 fix: de-duplication key was the first 3 words (too coarse).
        New fingerprint = action verb (first word) + first two content nouns.
        This correctly distinguishes "Click Save button." from "Click Save to save..."
        while still filtering true duplicates.

        Args:
            return_entries: if True returns List[Dict] with input+output context;
                            if False returns List[str] output strings (backward compat).
        """
        results = []
        seen_outputs: Set[str] = set()
        seen_fingerprints: Set[str] = set()

        for (
            doc_index,
            _score,
        ) in ranked_results[: top_k * 4]:

            entry = self.entries[doc_index]
            output = entry.get("output", "").strip()

            if not output or output in seen_outputs:
                continue

            # Smarter fingerprint: verb + significant words (skip stop words)
            words = [
                w for w in output.lower().split()
                if w not in self._stopwords and len(w) > 2
            ]
            fingerprint = " ".join(words[:4])  # 4 significant words

            if (
                diversity_weight > 0
                and fingerprint in seen_fingerprints
            ):
                continue

            seen_outputs.add(output)
            seen_fingerprints.add(fingerprint)

            if return_entries:
                inp = entry.get("input", {})
                results.append({
                    "action":  inp.get("action", ""),
                    "label":   inp.get("label", ""),
                    "value":   (
                        inp.get("value")
                        or inp.get("selectedText")
                        or ""
                    ),
                    "output":  output,
                })
            else:
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

        label = " ".join(label.split())

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
                for keyword in ["save", "submit", "apply"]
            ):
                boosts["save"] = 2.0

            elif any(
                keyword in label_lower
                for keyword in ["cancel", "close", "dismiss"]
            ):
                boosts["cancel"] = 2.0

            elif any(
                keyword in label_lower
                for keyword in ["tab", "menu", "nav"]
            ):
                boosts["navigation"] = 2.5

            else:
                boosts["action"] = 1.0

        elif action_lower in ["enter", "input", "type"]:
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
        """
        BUG 6 partial fix: give the 'output' field higher weight by
        including it twice so its terms appear more in term frequency.
        """
        inp = entry.get("input", {})

        parts = [
            entry.get("name", ""),
            entry.get("output", ""),
            entry.get("output", ""),   # double-weight output for TF
            inp.get("action", ""),
            inp.get("label", ""),
            inp.get("label", ""),      # double-weight label for TF
            inp.get("value", "") or "",
            inp.get("selectedText", "") or "",
            inp.get("placeholder", "") or "",
            inp.get("ariaLabel", "") or "",
        ]

        text = " ".join(str(part) for part in parts if part)

        tokens = self._tokenize(text)

        # Add bigrams for compound phrases (e.g., "study report", "workflow actions")
        bigrams = [
            f"{tokens[i]}_{tokens[i+1]}"
            for i in range(len(tokens) - 1)
            if len(tokens[i]) > 2 and len(tokens[i + 1]) > 2
        ]

        return tokens + bigrams

    def _tokenize(
        self,
        text: str,
    ) -> List[str]:
        """
        BUG 4 fix: uses self._stopwords (cached at __init__) instead of
        calling config.get() on every invocation.
        """
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
                and token not in self._stopwords
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
        self.value_index.clear()
        self.normalized_label_index.clear()
        self._loaded_files.clear()
        self.avg_doc_length = 0.0

    # ─────────────────────────────────────────────────────
    # Properties
    # ─────────────────────────────────────────────────────

    @property
    def loaded_files(self) -> List[str]:
        return self._loaded_files

    @property
    def total_entries(self) -> int:
        return len(self.entries)

    # ─────────────────────────────────────────────────────
    # Stats
    # ─────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:

        return {
            "total_entries": len(self.entries),
            "loaded_files": len(self._loaded_files),
            "action_types": len(self.action_clusters),
            "unique_labels": len(self.normalized_label_index),
            "unique_values": len(self.value_index),
            "avg_doc_length": round(self.avg_doc_length, 2),
            "index_terms": len(self.bm25_index),
            "action_boost_score": self._action_boost_score,
            "bm25_weight": self._bm25_weight,
            "tfidf_weight": self._tfidf_weight,
        }