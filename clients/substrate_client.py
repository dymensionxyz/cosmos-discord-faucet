import logging
import os
import sys
from typing import List
from substrateinterface import SubstrateInterface, Keypair
from substrateinterface.exceptions import SubstrateRequestException

from clients.faucet_client import FaucetClient, Balance, NodeStatus, NetworkDenomPair


class SubstrateClient(FaucetClient):

    def __init__(self, key, **args):
        super().__init__(key, **args)
        self.substrate = SubstrateInterface(url=self.node_ws)
        try:
            faucet_mnemonic = os.environ[self.faucet_mnemonic_key]
            self.keypair = Keypair.create_from_mnemonic(faucet_mnemonic)
        except KeyError as key:
            logging.critical('Faucet mnemonic could not be found: %s', key)
            sys.exit()

    def get_balance(self, address: str, original_denom: str) -> Balance:
        result = self.substrate.query('System', 'Account', [address])
        balance = Balance(self.node_denom, result.value['data']['free'])
        return balance

    def fetch_bech32_address(self, address: str) -> str:
        return address

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

    def fetch_network_denom_list(self, original_denom=False, cache=True) -> List[NetworkDenomPair]:
        return [NetworkDenomPair(self.node_chain_id, self.node_denom, self.node_denom)]

    def tx_send(self, sender: str, recipient: str, amount: str, fees: int) -> str:
        try:
            call = self.substrate.compose_call(
                call_module='Balances',
                call_function='transfer',
                call_params={'dest': recipient, 'value': 1000000000000000000}
            )
            extrinsic = self.substrate.create_signed_extrinsic(call=call, keypair=self.keypair, era={'period': 64})
            receipt = self.substrate.submit_extrinsic(extrinsic, wait_for_inclusion=True)
            print(f"Extrinsic '{receipt.extrinsic_hash}' sent and included in block '{receipt.block_hash}'")
            return "aaaa"
        except SubstrateRequestException as err:
            logging.critical('Failed to send tokens', err)
            raise err
    # """
    # dymd tx bank send <from address> <to address> <amount> <fees> <node> <chain-id> --keyring-backend=test -y
    # """
    # response = self.execute([
    #     'tx',
    #     'bank',
    #     'send',
    #     sender,
    #     recipient,
    #     amount,
    #     f'--fees={fees}{self.node_denom}',
    #     '--keyring-backend=test',
    #     '-y'
    # ])
    # try:
    #     logging.info("Tx Send response %s", response)
    #     return response['txhash']
    # except (TypeError, KeyError) as err:
    #     logging.critical('Could not read %s in tx response', err)
    #     raise err
