import subprocess
import json
import logging
from typing import List

from clients.faucet_client import FaucetClient, Balance, NodeStatus, NetworkDenomPair, TxInfo


class CosmosClient(FaucetClient):

    def execute(self, params, chain_id=True, json_output=True):
        params = [self.node_executable] + params + [f"--node={self.node_rpc}"]
        if chain_id:
            params.append(f"--chain-id={self.node_chain_id}")
        if json_output:
            params.append('--output=json')
        result = subprocess.run(params, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            result.check_returncode()
            return json.loads(result.stdout)
        except subprocess.CalledProcessError as cpe:
            output = str(result.stderr).split('\n', maxsplit=1)
            logging.error("Called Process Error: %s, stderr: %s", cpe, output)
            raise cpe

    def get_fixed_balance_denom(self, balance: Balance):
        if balance.denom.startswith('ibc/'):
            response = self.execute(["query", "ibc-transfer", "denom-trace", balance.denom])
            balance.original_denom = balance.denom
            balance.denom = response['denom_trace']['base_denom']
        return balance

    def get_balances(self, address: str) -> List[Balance]:
        """
        dymd query bank balances <address> <node> <chain-id>
        """
        try:
            response = self.execute(["query", "bank", "balances", address])
            return list(map(lambda balance: self.get_fixed_balance_denom(Balance(**balance)), response['balances']))
        except IndexError as index_error:
            logging.error('Parsing error on balance request: %s', index_error)
            raise index_error

    def get_node_status(self):
        """
        dymd status <node>
        """
        status = self.execute(["status"], chain_id=False, json_output=False)
        print("aaaaa", status)
        try:
            node_status = NodeStatus(
                str(status['NodeInfo']['moniker']),
                str(status['NodeInfo']['network']),
                int(status['SyncInfo']['latest_block_height']),
                bool(status['SyncInfo']['catching_up'])
            )
            return node_status
        except KeyError as key:
            logging.error('Key not found in node status: %s', key)
            raise key

    def check_address(self, address: str):
        """
        dymd keys parse <address>
        """
        check = subprocess.run(
            [self.node_executable, "keys", "parse", f"{address}", '--output=json'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True)
        try:
            check.check_returncode()
            return json.loads(check.stdout[:-1])
        except subprocess.CalledProcessError as cpe:
            output = str(check.stderr).split('\n', maxsplit=1)
            logging.error("Called Process Error: %s, stderr: %s", cpe, output)
            raise cpe
        except IndexError as index_error:
            logging.error('Parsing error on address check: %s', index_error)
            raise index_error

    def fetch_denom_from_trace(self, denom_trace, original_denom=False) -> NetworkDenomPair:
        path = denom_trace["path"]
        base_denom = str(denom_trace["base_denom"])
        path_parts = str(path).split("/")
        client_state = \
            self.execute(["query", "ibc", "channel", "client-state", "transfer", path_parts[len(path_parts) - 1]])
        result = NetworkDenomPair(str(client_state["client_state"]["chain_id"]), base_denom)

        if original_denom:
            denom_hash = self.execute(["query", "ibc-transfer", "denom-hash", f'{path}/{base_denom}'])
            result.original_denom = f'ibc/{denom_hash["hash"]}'

        return result

    def fetch_network_denom_list(self, original_denom=False) -> List[NetworkDenomPair]:
        response = self.execute(["query", "ibc-transfer", "denom-traces"])
        network_denom_list = list(map(
            lambda trace: self.fetch_denom_from_trace(trace, original_denom), response['denom_traces']))

        node_network_denom = NetworkDenomPair(self.node_chain_id, self.node_denom, self.node_denom)
        fixed_list = [node_network_denom]

        for network_denom in network_denom_list:
            exist_denom = next((item for item in fixed_list if item.denom == network_denom.denom), None)
            if not exist_denom:
                fixed_list.append(network_denom)

        return fixed_list

    def tx_send(self, sender: str, recipient: str, amount: str, fees: int) -> str:
        """
        dymd tx bank send <from address> <to address> <amount> <fees> <node> <chain-id> --keyring-backend=test -y
        """
        response = self.execute([
            'tx',
            'bank',
            'send',
            sender,
            recipient,
            amount,
            f'--fees={fees}{self.node_denom}',
            '--keyring-backend=test',
            '-y'
        ])
        try:
            logging.info("Tx Send response %s", response)
            return response['txhash']
        except (TypeError, KeyError) as err:
            logging.critical('Could not read %s in tx response', err)
            raise err

    def get_tx_info(self, hash_id: str) -> TxInfo:
        """
        dymd query tx <tx-hash> <node> <chain-id>
        """
        tx_response = self.execute(['query', 'tx', f'{hash_id}'])
        try:
            tx_body = tx_response['tx']['body']['messages'][0]
            height = int(tx_response['height'])
            if 'from_address' in tx_body.keys():
                tx_info = TxInfo(
                    height,
                    tx_body['from_address'],
                    tx_body['to_address'],
                    tx_body['amount'][0]['amount'] + tx_body['amount'][0]['denom'])
            elif 'sender' in tx_body.keys():
                tx_info = TxInfo(
                    height,
                    tx_body['sender'],
                    tx_body['receiver'],
                    tx_body['token']['amount'] + tx_body['token']['denom'])
            else:
                logging.error(
                    "Neither 'from_address' nor 'sender' key was found in response body:\n%s", tx_body)
                raise ValueError("Invalid tx response query")
            return tx_info
        except (TypeError, KeyError) as err:
            logging.critical('Could not read %s in raw log.', err)
            raise KeyError from err
