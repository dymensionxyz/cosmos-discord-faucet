import re


class FaucetEnv:
    def __init__(
            self,
            key,
            node_rpc,
            node_executable,
            node_denom,
            node_chain_id,
            network_name,
            faucet_address,
            address_prefix,
            amount_to_send,
            amount_to_send_evm,
            daily_cap,
            daily_cap_evm,
            tx_fees,
            block_explorer_tx,
            token_requests_cap,
            ibc_token_requests_cap,
            ibc_enabled,
            channels_to_listen,
            request_timeout
    ):
        self.key = str(key)
        self.node_rpc = str(node_rpc)
        self.node_executable = str(node_executable)
        self.node_denom = str(node_denom)
        self.node_chain_id = str(node_chain_id)
        self.network_name = str(network_name)
        self.faucet_address = str(faucet_address)
        self.address_prefix = str(address_prefix)
        self.amount_to_send = int(amount_to_send)
        self.amount_to_send_evm = int(amount_to_send_evm)
        self.daily_cap = int(daily_cap)
        self.daily_cap_evm = int(daily_cap_evm)
        self.tx_fees = int(tx_fees)
        self.block_explorer_tx = str(block_explorer_tx)
        self.token_requests_cap = int(token_requests_cap)
        self.ibc_token_requests_cap = int(ibc_token_requests_cap)
        self.ibc_enabled = bool(ibc_enabled)
        self.channels_to_listen = list(channels_to_listen.split(','))
        self.request_timeout = int(request_timeout)

    def get_amount_to_send(self, network_id: str):
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


def is_evm_network(network_id: str):
    """
    Returns whether the specified network is evm related
    """
    return bool(re.search("^[^_-]+_[0-9]+[_-][0-9]+$", network_id))
