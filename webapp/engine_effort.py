"""Versioned effort policy. Budgets are limits, never accuracy guarantees."""
from dataclasses import asdict, dataclass

POLICY_VERSION = 'adaptive-v10'
LEVELS = ('low', 'mid', 'high', 'xhigh', 'max', 'ultra')
# minimum nodes for early stop, max seconds, minimum depth, stable iterations, cp spread
BUDGETS = (
    (62_500, 1.0, 10, 3, 25),
    (250_000, 5.0, 12, 3, 18),
    # Midpoint rounded upward: whole seconds, 10,000 nodes, whole iterations.
    (630_000, 8.0, 13, 4, 15),
    (1_000_000, 10.0, 14, 4, 12),
    (1_000_000, 30.0, 14, 5, 8),
    (1_000_000, 60.0, 14, 6, 5),
)


@dataclass(frozen=True)
class EffortPolicy:
    effort: str
    max_time_s: float
    min_depth: int
    stable_iterations: int
    score_tolerance_cp: int
    min_nodes: int


def policy_for(effort: str) -> EffortPolicy:
    if effort not in LEVELS:
        raise ValueError('effort must be one of: ' + ', '.join(LEVELS))
    min_nodes, seconds, depth, iterations, tolerance = BUDGETS[LEVELS.index(effort)]
    return EffortPolicy(effort, seconds, depth, iterations, tolerance, min_nodes)


def policy_dict(effort):
    return {'version': POLICY_VERSION, **asdict(policy_for(effort))}


class StabilityTracker:
    """Only compare exact, complete, distinct root-depth observations."""
    def __init__(self, policy):
        self.policy = policy
        self.samples = []

    def update(self, info):
        if (info.get('multipv', 1) != 1 or info.get('lowerbound') or info.get('upperbound')
                or 'score' not in info or not info.get('pv') or 'depth' not in info):
            return False
        depth = info['depth']
        if self.samples and depth <= self.samples[-1][0]:
            return False
        score = info['score'].white()
        value = ('mate', score.mate()) if score.is_mate() else ('cp', score.score())
        self.samples.append((depth, info['pv'][0], value))
        self.samples = self.samples[-self.policy.stable_iterations:]
        if (len(self.samples) < self.policy.stable_iterations or depth < self.policy.min_depth
                or info.get('nodes', 0) < self.policy.min_nodes):
            return False
        if len({s[1] for s in self.samples}) != 1 or len({s[2][0] for s in self.samples}) != 1:
            return False
        values = [s[2][1] for s in self.samples]
        if value[0] == 'mate':
            return len(set(values)) == 1
        return max(values) - min(values) <= self.policy.score_tolerance_cp


OFFICIAL_POLICY_VERSION = 'stockfish-time-v1'

def official_policy_dict(seconds):
    return {'version': OFFICIAL_POLICY_VERSION,
            'max_time_s': seconds,
            'remaining_moves': 1}
