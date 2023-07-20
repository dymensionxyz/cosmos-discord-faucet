import re
from enum import Enum
from typing import List


# class syntax
class FaucetClientType(Enum):
    COSMOS = 1
    SUBSTRATE = 2


class Balance:
    def __init__(self, denom: str, amount: float, original_denom: str = None):
        self.denom = denom
        self.original_denom = original_denom or denom
        self.amount = amount


class NodeStatus:
    def __init__(self, moniker: str, chain: str, last_block: int, syncs: bool):
        self.moniker = moniker
        self.chain = chain
        self.last_block = last_block
        self.syncs = syncs


class NetworkDenomPair:
    def __init__(self, network_id: str, denom: str, original_denom: str = None):
        self.network_id = network_id
        self.denom = denom
        self.original_denom = original_denom or denom


class TxInfo:
    def __init__(self, height: int, sender: str, receiver: str, amount: int):
        self.height = height
        self.sender = sender
        self.receiver = receiver
        self.amount = amount


class FaucetClient:
    def __init__(
            self,
            key,
            node_denom,
            node_chain_id,
            network_name,
            amount_to_send,
            daily_cap,
            tx_fees,
            token_requests_cap,
            ibc_enabled,
            channels_to_listen,
            request_timeout,
            block_explorer_tx="",
            faucet_address="",
            faucet_mnemonic_key="",
            ibc_token_requests_cap=0,
            amount_to_send_evm=0,
            daily_cap_evm=0,
            node_ws="",
            node_rpc="",
            node_executable="",
            address_prefix="",
    ):
        self.key = key
        self.node_rpc = node_rpc
        self.node_ws = node_ws
        self.node_executable = node_executable
        self.node_denom = node_denom
        self.node_chain_id = node_chain_id
        self.network_name = network_name
        self.faucet_address = faucet_address
        self.faucet_mnemonic_key = faucet_mnemonic_key
        self.address_prefix = address_prefix
        self.amount_to_send = int(amount_to_send)
        self.amount_to_send_evm = int(amount_to_send_evm)
        self.daily_cap = int(daily_cap)
        self.daily_cap_evm = int(daily_cap_evm)
        self.tx_fees = int(tx_fees)
        self.block_explorer_tx = block_explorer_tx
        self.token_requests_cap = int(token_requests_cap)
        self.ibc_token_requests_cap = int(ibc_token_requests_cap)
        self.ibc_enabled = bool(ibc_enabled)
        self.channels_to_listen = list(channels_to_listen.split(','))
        self.request_timeout = int(request_timeout)

    def get_amount_to_send(self, network_id: str) -> int:
        """
        Returns the amount_to_send according to the specified network
        """
        if is_evm_network(network_id):
            return self.amount_to_send_evm
        return self.amount_to_send

    def get_daily_cap(self, network_id: str):
        """
        Returns the daily_cap according to the specified network
        """
        if is_evm_network(network_id):
            return self.daily_cap_evm
        return self.daily_cap

    def get_token_requests_cap(self, network_id: str):
        if network_id == self.node_chain_id:
            return self.token_requests_cap
        return self.ibc_token_requests_cap

    def get_balances(self, address: str) -> List[Balance]:
        pass

    def get_node_status(self) -> NodeStatus:
        pass

    def fetch_bech32_address(self, address: str) -> str:
        pass

    def check_address(self, address: str):
        pass

    def fetch_network_denom_list(self, original_denom=False, cache=True) -> List[NetworkDenomPair]:
        pass

    def tx_send(self, sender: str, recipient: str, amount: str, fees: int) -> str:
        pass

    def get_tx_info(self, hash_id: str) -> TxInfo:
        pass


def is_evm_network(network_id: str):
    """
    Returns whether the specified network is evm related
    """
    return bool(re.search("^[^_-]+_[0-9]+[_-][0-9]+$", network_id))
