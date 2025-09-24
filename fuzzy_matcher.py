"""
Fuzzy string matching utility for company name matching with user confirmation.
"""
import difflib
from typing import List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class FuzzyMatch:
    """Represents a fuzzy match result."""
    target: str
    match: str
    confidence: float
    data: any = None  # Store additional data (e.g., company_id)


class FuzzyMatcher:
    """Fuzzy string matcher for company names."""
    
    def __init__(self, confidence_threshold: float = 0.6):
        """
        Initialize fuzzy matcher.
        
        Args:
            confidence_threshold: Minimum confidence score (0.0-1.0) for a match
        """
        self.confidence_threshold = confidence_threshold
    
    def normalize_name(self, name: str) -> str:
        """
        Normalize company name for better matching.
        
        Args:
            name: Company name to normalize
            
        Returns:
            Normalized company name
        """
        # Convert to uppercase and remove common company suffixes
        normalized = name.upper().strip()
        
        # Remove common suffixes
        suffixes = ["LIMITED", "LTD", "LTD.", "PRIVATE", "PVT", "PVT.", 
                   "CORPORATION", "CORP", "CORP.", "INC", "INC.", 
                   "COMPANY", "CO", "CO."]
        
        for suffix in suffixes:
            if normalized.endswith(f" {suffix}"):
                normalized = normalized[:-len(suffix)-1].strip()
        
        # Remove extra spaces
        normalized = " ".join(normalized.split())
        
        return normalized
    
    def calculate_similarity(self, target: str, candidate: str) -> float:
        """
        Calculate similarity between two strings using difflib.
        
        Args:
            target: Target string
            candidate: Candidate string to compare
            
        Returns:
            Similarity score (0.0-1.0)
        """
        target_norm = self.normalize_name(target)
        candidate_norm = self.normalize_name(candidate)
        
        # Use difflib's SequenceMatcher for similarity calculation
        return difflib.SequenceMatcher(None, target_norm, candidate_norm).ratio()
    
    def find_best_matches(self, target: str, candidates: dict, 
                         max_matches: int = 3) -> List[FuzzyMatch]:
        """
        Find best fuzzy matches for a target string.
        
        Args:
            target: Target string to match
            candidates: Dictionary of candidate strings -> data
            max_matches: Maximum number of matches to return
            
        Returns:
            List of FuzzyMatch objects sorted by confidence (highest first)
        """
        matches = []
        
        for candidate, data in candidates.items():
            confidence = self.calculate_similarity(target, candidate)
            
            if confidence >= self.confidence_threshold:
                matches.append(FuzzyMatch(
                    target=target,
                    match=candidate,
                    confidence=confidence,
                    data=data
                ))
        
        # Sort by confidence (highest first) and return top matches
        matches.sort(key=lambda x: x.confidence, reverse=True)
        return matches[:max_matches]
    
    def get_best_match(self, target: str, candidates: dict) -> Optional[FuzzyMatch]:
        """
        Get the single best fuzzy match for a target string.
        
        Args:
            target: Target string to match
            candidates: Dictionary of candidate strings -> data
            
        Returns:
            Best FuzzyMatch or None if no match meets threshold
        """
        matches = self.find_best_matches(target, candidates, max_matches=1)
        return matches[0] if matches else None
