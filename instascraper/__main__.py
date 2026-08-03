"""Enable `python -m instascraper`."""

import sys

from instascraper.cli import main

if __name__ == "__main__":
    sys.exit(main())
