"""
LLM provider abstraction for fact extraction and comparison.
Production code should not be hard-coded around a single provider.
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from app.models import Fact, FactComparison, Dimensions


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def extract_facts(self, text: str, page_context: str) -> List[Fact]:
        """
        Extract grounded facts from a page of text.

        Args:
            text: raw page text
            page_context: human-readable context (document name, page number)

        Returns:
            List of extracted Fact objects with evidence populated.
        """
        ...

    @abstractmethod
    def compare_facts(self, fact_a: Fact, fact_b: Fact) -> FactComparison:
        """
        Classify the relationship between two facts.

        Returns:
            FactComparison with relationship, confidence, reason, and dimensions.
        """
        ...


class MockProvider(LLMProvider):
    """
    Deterministic mock provider for testing.
    Returns fixture-based facts without calling any external API.
    """

    def __init__(self, facts: Optional[List[Fact]] = None, comparisons: Optional[List[FactComparison]] = None):
        self._facts = facts or []
        self._comparisons = comparisons or []
        self._extract_calls: list = []
        self._compare_calls: list = []

    def extract_facts(self, text: str, page_context: str) -> List[Fact]:
        self._extract_calls.append({"text": text, "page_context": page_context})
        return self._facts

    def compare_facts(self, fact_a: Fact, fact_b: Fact) -> FactComparison:
        self._compare_calls.append({"fact_a": fact_a, "fact_b": fact_b})
        if self._comparisons:
            return self._comparisons[0]
        return FactComparison(
            fact_a_id=fact_a.id,
            fact_b_id=fact_b.id,
            relationship="UNCERTAIN",
            confidence=0.0,
            reason="Mock provider — no comparison logic",
            dimensions=Dimensions(
                subject="unknown", predicate="unknown", value="unknown",
                unit="unknown", period="unknown", scope="unknown", qualifiers="unknown"
            ),
        )

    @property
    def extract_call_count(self) -> int:
        return len(self._extract_calls)

    @property
    def compare_call_count(self) -> int:
        return len(self._compare_calls)
