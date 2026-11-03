import sys

from timonel import demo_agent
from timonel.cli import main

if sys.argv[1:3] == ["-m", "timonel.demo_agent"]:
    sys.argv = [sys.argv[0], *sys.argv[3:]]
    sys.exit(demo_agent.main())
sys.exit(main())
