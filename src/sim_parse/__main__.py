"""Allow `python -m sim_parse <case>` invocation."""
from sim_parse.cli.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
