"""Match extracted subtitle dialogue to canonical episode transcripts.

Uses `rapidfuzz.fuzz.token_set_ratio` for per-pair similarity and
`scipy.optimize.linear_sum_assignment` (the Hungarian algorithm) to find the
globally optimal one-to-one mapping. Without the Hungarian step, two files that
both look similar to the same episode would both be assigned there, leaving
another episode orphaned.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileSample:
    """A subtitle sample taken from one MKV file."""

    path: Path
    dialogue: str


@dataclass(frozen=True)
class EpisodeReference:
    """A canonical episode transcript from a provider."""

    season: int
    episode: int
    title: str
    transcript: str


@dataclass(frozen=True)
class Candidate:
    """One possible match for a file with its similarity score."""

    episode: EpisodeReference
    score: float  # 0.0 to 100.0, higher is better


@dataclass(frozen=True)
class Match:
    """The final assignment for one file plus a few alternatives for review."""

    file: FileSample
    best: Candidate
    alternates: tuple[Candidate, ...]

    @property
    def confidence(self) -> float:
        return self.best.score

    @property
    def needs_review(self) -> bool:
        """True if confidence is low OR runner-up is suspiciously close."""
        if not self.alternates:
            return self.confidence < 70
        gap = self.confidence - self.alternates[0].score
        return self.confidence < 70 or gap < 5


def score_pair(file_dialogue: str, episode_transcript: str) -> float:
    """Similarity between a file's sampled dialogue and an episode's transcript.

    `token_set_ratio` is robust to ordering differences and extra text, which
    matters because the file sample is short and the full transcript is long.
    """
    if not file_dialogue or not episode_transcript:
        return 0.0
    return float(fuzz.token_set_ratio(file_dialogue, episode_transcript))


def build_cost_matrix(
    files: list[FileSample],
    episodes: list[EpisodeReference],
) -> np.ndarray:
    """Build an [n_files x n_episodes] cost matrix.

    Cost = 100 - similarity, so the Hungarian solver (which minimises cost)
    maximises total similarity across the assignment.
    """
    n_f = len(files)
    n_e = len(episodes)
    matrix = np.full((n_f, n_e), 100.0)
    for i, f in enumerate(files):
        for j, e in enumerate(episodes):
            matrix[i, j] = 100.0 - score_pair(f.dialogue, e.transcript)
    return matrix


def match(
    files: list[FileSample],
    episodes: list[EpisodeReference],
    top_k_alternates: int = 2,
) -> list[Match]:
    """Assign each file to its best episode using Hungarian assignment.

    The number of episodes does not need to equal the number of files;
    `linear_sum_assignment` handles rectangular matrices (extra rows/columns
    simply go unassigned).
    """
    if not files:
        return []
    if not episodes:
        raise ValueError("no episodes to match against")

    cost = build_cost_matrix(files, episodes)
    row_ind, col_ind = linear_sum_assignment(cost)
    assigned = dict(zip(row_ind.tolist(), col_ind.tolist(), strict=True))

    matches: list[Match] = []
    for i, file in enumerate(files):
        j = assigned.get(i)
        if j is None:
            # More files than episodes — file unassigned. Skip; caller can
            # inspect the missing entries separately if needed.
            log.warning("no episode slot for %s", file.path.name)
            continue

        # Build the candidate list for this file, ranked by raw score
        # (independent of the global assignment).
        per_file_scores = [(idx, float(100.0 - cost[i, idx])) for idx in range(len(episodes))]
        per_file_scores.sort(key=lambda x: x[1], reverse=True)

        best = Candidate(episode=episodes[j], score=float(100.0 - cost[i, j]))

        alternates: list[Candidate] = []
        for idx, score in per_file_scores:
            if idx == j:
                continue
            alternates.append(Candidate(episode=episodes[idx], score=float(score)))
            if len(alternates) >= top_k_alternates:
                break

        matches.append(Match(file=file, best=best, alternates=tuple(alternates)))

    return matches
