"""Fetch modules: each talks to the local transfermarkt-api (or TM directly
where the API cannot provide the data) and returns structured records.

All network access goes through the shared client (rate-limited + cached).
"""

from . import clubs, coaches, competitions, market_values, profiles, rosters

__all__ = ["clubs", "coaches", "competitions", "market_values", "profiles", "rosters"]