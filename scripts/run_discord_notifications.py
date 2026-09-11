"""Send Discord activity/drop alerts independently of the farmer process."""
from conquest.discord_notify import run

if __name__=='__main__':
    run()
