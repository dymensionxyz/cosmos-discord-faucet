"""
dymd utility functions
- query bank balance
- query tx
- node status
- tx bank send
"""

import json
import subprocess
import logging
import toml
import sys

# Load config
config = toml.load('config.toml')

try:
    HUB_RPC = config['hub_rpc']
    HUB_CHAIN_ID = config['hub_chain_id']
    HUB_EXECUTABLE = config['hub_executable']
    HUB_DENOM = config['hub_denom']
except KeyError as key:
    logging.critical('Key could not be found: %s', key)
    sys.exit()


def execute(params, chain_id=True, json_output=True):
    params = [HUB_EXECUTABLE] + params + [f"--node={HUB_RPC}"]
    if chain_id:
        params.append(f"--chain-id={HUB_CHAIN_ID}")
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


def check_address(address: str):
    """
    dymd keys parse <address>
    """
    check = subprocess.run(
        [HUB_EXECUTABLE, "keys", "parse", f"{address}", '--output=json'],
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


def fetch_network_and_denom_from_trace(denom_trace, original_denom=False):
    path = denom_trace["path"]
    base_denom = denom_trace["base_denom"]
    path_parts = str(path).split("/")
    client_state = execute(["query", "ibc", "channel", "client-state", "transfer", path_parts[len(path_parts) - 1]])
    result = {"network_id": client_state["client_state"]["chain_id"], "denom": base_denom}

    if original_denom:
        denom_hash = execute(["query", "ibc-transfer", "denom-hash", f'{path}/{base_denom}'])
        result['original_denom'] = f'ibc/{denom_hash["hash"]}'

    return result


def fetch_network_denom_list(original_denom=False):
    response = execute(["query", "ibc-transfer", "denom-traces"])
    network_denom_list = list(map(
        lambda trace: fetch_network_and_denom_from_trace(trace, original_denom), response['denom_traces']))
    network_denom_list.append({"network_id": HUB_CHAIN_ID, "denom": HUB_DENOM})
    return network_denom_list


def get_fixed_balance_denom(balance):
    denom = balance["denom"]
    if denom.startswith('ibc/'):
        response = execute(["query", "ibc-transfer", "denom-trace", denom])
        balance['denom'] = response['denom_trace']['base_denom']
    balance['amount'] = float(balance['amount'])
    return balance


def get_balances(address: str):
    """
    dymd query bank balances <address> <node> <chain-id>
    """
    try:
        response = execute(["query", "bank", "balances", address])
        balances = response['balances']
        return list(map(get_fixed_balance_denom, balances))
    except IndexError as index_error:
        logging.error('Parsing error on balance request: %s', index_error)
        raise index_error


def get_node_status():
    """
    dymd status <node>
    """
    status = execute(["status"], chain_id=False, json_output=False)
    try:
        node_status = {
            'moniker': status['NodeInfo']['moniker'],
            'chain': status['NodeInfo']['network'],
            'last_block': status['SyncInfo']['latest_block_height'],
            'syncs': status['SyncInfo']['catching_up']
        }
        return node_status
    except KeyError as key:
        logging.error('Key not found in node status: %s', key)
        raise key


def get_tx_info(hash_id: str):
    """
    dymd query tx <tx-hash> <node> <chain-id>
    """
    tx_response = execute(['query', 'tx', f'{hash_id}'])
    try:
        tx_body = tx_response['tx']['body']['messages'][0]
        tx_out = {'height': tx_response['height']}
        if 'from_address' in tx_body.keys():
            tx_out['sender'] = tx_body['from_address']
            tx_out['receiver'] = tx_body['to_address']
            tx_out['amount'] = tx_body['amount'][0]['amount'] + tx_body['amount'][0]['denom']
        elif 'sender' in tx_body.keys():
            tx_out['sender'] = tx_body['sender']
            tx_out['receiver'] = tx_body['receiver']
            tx_out['amount'] = tx_body['token']['amount'] + tx_body['token']['denom']
        else:
            logging.error(
                "Neither 'from_address' nor 'sender' key was found in response body:\n%s", tx_body)
            return None
        return tx_out
    except (TypeError, KeyError) as err:
        logging.critical('Could not read %s in raw log.', err)
        raise KeyError from err


def tx_send(sender: str, recipient: str, amount: str, fees: int):
    """
    dymd tx bank send <from address> <to address> <amount> <fees> <node> <chain-id> --keyring-backend=test -y
    """
    response = execute([
        'tx',
        'bank',
        'send',
        sender,
        recipient,
        amount,
        f'--fees={fees}{HUB_DENOM}',
        '--keyring-backend=test',
        '-y'
    ])
    try:
        return response['txhash']
    except (TypeError, KeyError) as err:
        logging.critical('Could not read %s in tx response', err)
        raise err
