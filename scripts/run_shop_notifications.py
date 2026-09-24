"""Watch merchant failures and send #shops alerts independently of Conquest."""

from _bootstrap import activate

activate(__file__)
from conquest.merchants.alerts import run

if __name__ == "__main__":
    run()
