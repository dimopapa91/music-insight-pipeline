"""Safety checks of scripts/x402_testnet_pay.py (no network, no key)."""

import importlib.util
import pathlib

import pytest

_path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "x402_testnet_pay.py"
_spec = importlib.util.spec_from_file_location("x402_testnet_pay", _path)
pay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pay)

GOOD = {
    "scheme": "exact",
    "network": "eip155:84532",
    "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    "amount": "5000",
    "payTo": "0x" + "1" * 40,
}


def _opt(**over):
    option = dict(GOOD)
    option.update(over)
    return option


def test_accepts_the_expected_testnet_option():
    assert pay.select_requirement([_opt()]) == GOOD


def test_refuses_any_other_network():
    with pytest.raises(pay.Abort):
        pay.select_requirement([_opt(network="eip155:8453")])   # Base mainnet


def test_picks_the_testnet_option_when_mainnet_is_also_offered():
    chosen = pay.select_requirement([_opt(network="eip155:8453"), _opt()])
    assert chosen["network"] == "eip155:84532"


def test_refuses_unknown_asset_and_other_schemes():
    with pytest.raises(pay.Abort):
        pay.select_requirement([_opt(asset="0x" + "9" * 40)])
    with pytest.raises(pay.Abort):
        pay.select_requirement([_opt(scheme="upto")])


@pytest.mark.parametrize("amount", ["10001", "1000000", "0", "-1", "abc", None])
def test_amount_cap_and_sanity(amount):
    with pytest.raises(pay.Abort):
        pay.select_requirement([_opt(amount=amount)])


def test_cap_is_inclusive_at_one_cent():
    assert pay.select_requirement([_opt(amount="10000")])["amount"] == "10000"


def test_expected_pay_to_must_match():
    with pytest.raises(pay.Abort):
        pay.select_requirement([_opt()], expected_pay_to="0x" + "2" * 40)
    assert pay.select_requirement([_opt()], expected_pay_to=("0x" + "1" * 40).upper().replace("0X", "0x"))


def test_empty_accepts_aborts():
    with pytest.raises(pay.Abort):
        pay.select_requirement([])


def test_key_never_hardcoded_in_script():
    import re
    source = _path.read_text()
    assert not re.search(r"0x[0-9a-fA-F]{64}", source)
