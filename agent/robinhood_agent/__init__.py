"""An autonomous trading agent for a Robinhood account.

Safety model, in short:
  - `paper` mode is the default and never touches an order endpoint.
  - `live` mode needs two independent keys (config + environment variable).
  - Every trade passes a stack of risk guards that can only reject.
  - A halt file stops everything, immediately, from any process.
  - Every decision is appended to a journal that is never rewritten.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
