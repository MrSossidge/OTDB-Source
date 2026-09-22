"""OpenTDB Downloader — PortaPak Edition.

A resumable, rate-limited downloader for the Open Trivia Database (OpenTDB)
that preserves complete question records and produces validated JSON for
the PortaPak Quiz application.

Derived from OTDB-Source by QuartzWarrior
(https://github.com/QuartzWarrior/OTDB-Source), licensed under GPL-3.0.
"""

__version__ = "2.0.0"

OPENTDB_BASE_URL = "https://opentdb.com"
OPENTDB_MIN_INTERVAL = 5.0  # seconds; "Each IP can only access the API once every 5 seconds."
OPENTDB_MAX_AMOUNT = 50  # "A Maximum of 50 Questions can be retrieved per call."
