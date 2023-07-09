import logging
from typing import List

from substrateinterface import SubstrateInterface

from clients.faucet_client import FaucetClient, Balance, NodeStatus


class SubstrateClient(FaucetClient):

    def __init__(self, key, **args):
        super().__init__(key, **args)
        self.substrate = SubstrateInterface(url=self.node_ws)

    def get_balances(self, address: str) -> List[Balance]:
        result = self.substrate.query('System', 'Account', [address])
        balance = Balance(self.node_denom, result.value['data']['free'])
        return [balance]

    def get_node_status(self):
        try:
            node_status = NodeStatus(
                "aa",
                "bb",
                32,
                False
            )
            return node_status
        except KeyError as key:
            logging.error('Key not found in node status: %s', key)
            raise key
