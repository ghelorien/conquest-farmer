import pytest
from conquest.town_trade import TownTrade, TownObservationUnavailable
from conquest.merchants.memory import GuiObservationChanged


@pytest.mark.parametrize(
    "message",
    [
        "GUI registry changed",
        "GUI geometry changed",
        "GUI table changed during observation",
    ],
)
def test_gui_race_before_any_input_is_retryable(message):
    trade = object.__new__(TownTrade)

    def execute(body):
        raise GuiObservationChanged(message)

    trade.execute = execute
    with pytest.raises(TownObservationUnavailable):
        trade({"action": "warehouse-money"})
    assert trade.input_attempted is False


def test_gui_race_after_input_is_not_retryable():
    trade = object.__new__(TownTrade)
    error = GuiObservationChanged("GUI registry changed")

    def execute(body):
        trade.input_attempted = True
        raise error

    trade.execute = execute
    with pytest.raises(GuiObservationChanged) as caught:
        trade({"action": "warehouse-money-withdraw"})
    assert caught.value is error


def test_invalid_registry_is_not_a_transient_race():
    trade = object.__new__(TownTrade)

    def execute(body):
        raise ValueError("Invalid GUI registry")

    trade.execute = execute
    with pytest.raises(ValueError) as caught:
        trade({"action": "warehouse-money"})
    assert not isinstance(caught.value, TownObservationUnavailable)
