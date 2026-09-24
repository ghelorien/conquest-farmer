"""Send Discord activity/drop alerts independently of the farmer process."""

from _bootstrap import activate

activate(__file__)
from conquest.discord_notify import run

if __name__ == "__main__":
    run()
