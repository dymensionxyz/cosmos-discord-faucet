"""
dymd utility functions
- query bank balance
- query tx
- node status
- tx bank send
"""
import json
import re
import subprocess
import logging

from faucet_types import FaucetEnv


def execute(env: FaucetEnv, params, chain_id=True, json_output=True, json_node=True):
    params = [env.node_executable] + params
    if json_node:
        params.append(f"--node={env.node_rpc}")
    if chain_id:
        params.append(f"--chain-id={env.node_chain_id}")
    if json_output:
        params.append('--output=json')
    result = subprocess.run(params, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        result.check_returncode()
        if json_output:
            return json.loads(result.stdout)
        return result.stdout
    except subprocess.CalledProcessError as cpe:
        output = str(result.stderr).split('\n', maxsplit=1)
        logging.error("Called Process Error: %s, stderr: %s", cpe, output)
        raise cpe


def check_address(env: FaucetEnv, address: str):
    """
    dymd keys parse <address>
    """
    check = subprocess.run(
        [env.node_executable, "keys", "parse", f"{address}", '--output=json'],
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


def fetch_denom_from_trace(env: FaucetEnv, denom_trace, original_denom=False):
    path = denom_trace["path"]
    base_denom = denom_trace["base_denom"]
    path_parts = str(path).split("/")
    client_state = \
        execute(env, ["query", "ibc", "channel", "client-state", "transfer", path_parts[len(path_parts) - 1]])
    result = {"network_id": client_state["client_state"]["chain_id"], "denom": base_denom}

    if original_denom:
        denom_hash = execute(env, ["query", "ibc-transfer", "denom-hash", f'{path}/{base_denom}'])
        result['original_denom'] = f'ibc/{denom_hash["hash"]}'

    return result


def fetch_bech32_address(env: FaucetEnv, address: str) -> str:
    if not address.startswith('0x'):
        return address

    response = execute(
        env, ['debug', 'addr', address.removeprefix('0x')], chain_id=False, json_output=False, json_node=False)
    match = re.search(r'Bech32 Acc: [^\s]+', response)
    if match:
        address = match.group().removeprefix('Bech32 Acc: ')
        print(address)

    return address


def fetch_network_denom_list(env: FaucetEnv, original_denom=False):
    response = execute(env, ["query", "ibc-transfer", "denom-traces"])
    network_denom_list = list(map(
        lambda trace: fetch_denom_from_trace(env, trace, original_denom), response['denom_traces']))

    node_network_denom = {"network_id": env.node_chain_id, "denom": env.node_denom}
    if original_denom:
        node_network_denom['original_denom'] = env.node_denom
    fixed_list = [node_network_denom]

    for network_denom in network_denom_list:
        exist_denom = next((item for item in fixed_list if item["denom"] == network_denom['denom']), None)
        if not exist_denom:
            fixed_list.append(network_denom)

    return fixed_list


def get_fixed_balance_denom(env: FaucetEnv, balance):
    denom = balance["denom"]
    balance['original_denom'] = denom
    if denom.startswith('ibc/'):
        response = execute(env, ["query", "ibc-transfer", "denom-trace", denom])
        balance['denom'] = response['denom_trace']['base_denom']
    balance['amount'] = float(balance['amount'])
    return balance


def get_balances(env: FaucetEnv, address: str):
    """
    dymd query bank balances <address> <node> <chain-id>
    """
    try:
        response = execute(env, ["query", "bank", "balances", address])
        balances = response['balances']
        return list(map(lambda balance: get_fixed_balance_denom(env, balance), balances))
    except IndexError as index_error:
        logging.error('Parsing error on balance request: %s', index_error)
        raise index_error


def get_node_status(env: FaucetEnv):
    """
    dymd status <node>
    """
    status = execute(env, ["status"], chain_id=False, json_output=False)
    status = json.loads(status)
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


def get_tx_info(env: FaucetEnv, hash_id: str):
    """
    dymd query tx <tx-hash> <node> <chain-id>
    """
    tx_response = execute(env, ['query', 'tx', f'{hash_id}'])
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


def tx_send(env: FaucetEnv, sender: str, recipient: str, amount: str, fees: int):
    """
    dymd tx bank send <from address> <to address> <amount> <fees> <node> <chain-id> --keyring-backend=test -y
    """
    response = execute(env, [
        'tx',
        'bank',
        'send',
        sender,
        recipient,
        amount,
        f'--fees={fees}{env.node_denom}',
        '--keyring-backend=test',
        '-y'
    ])
    try:
        logging.info("Tx Send response %s", response)
        return response['txhash']
    except (TypeError, KeyError) as err:
        logging.critical('Could not read %s in tx response', err)
        raise err
