"""
Sets up a Discord bot to provide info and tokens

"""
import time
import datetime
import logging
import sys
import aiofiles as aiof
import toml
import discord
import os
from tabulate import tabulate

from clients.cosmos_client import CosmosClient
from clients.faucet_client import FaucetClient, FaucetClientType
from clients.substrate_client import SubstrateClient

# Turn Down Discord Logging
disc_log = logging.getLogger('discord')
disc_log.setLevel(logging.INFO)

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# Load config
config = toml.load('config.toml')
envs = config['envs']


def create_client(env_key) -> FaucetClient:
    env = envs[env_key]
    client_type = FaucetClientType.__members__.get(env['client_type'])
    if not client_type:
        raise AttributeError("Unsupported client_type: " + env['client_type'])

    del env['client_type']
    if client_type == FaucetClientType.COSMOS:
        return CosmosClient(env_key, **env)
    elif client_type == FaucetClientType.SUBSTRATE:
        return SubstrateClient(env_key, **env)


try:
    CLIENTS = list(map(create_client, envs))
    CORE_TEAM_ROLE_ID = config['core_team_role_id']
    DISCORD_TOKEN = os.environ['DISCORD_TOKEN']
    ACTIVE_REQUESTS = {env: {} for env in envs}
    NETWORKS_DAY_TALLY = {env: {} for env in envs}
except KeyError as key:
    logging.critical('Key could not be found: %s', key)
    sys.exit()

APPROVE_EMOJI = '✅'
REJECT_EMOJI = '🚫'
WARNING_EMOJI = '❗'
GENERIC_ERROR_MESSAGE = f'{WARNING_EMOJI} Could not handle your request'

intents = discord.Intents.all()
discord_client = discord.Client(intents=intents)


def get_help_message(client: FaucetClient):
    message = '**List of available commands:**\n'
    message_index = 1

    if client.ibc_enabled:
        message += f'{message_index}. Lists all tokens available in the faucet:\n`$faucet_tokens`\n\n'
        message_index += 1

    message += f'{message_index}. Request tokens through the faucet:\n'
    if client.ibc_enabled:
        message += f'`$request [{client.network_name} address] <optional:network-id>`\n\n'
    else:
        message += f'`$request [address]`\n\n'
    message_index += 1

    message += f'{message_index}. Request the faucet and node status:\n`$faucet_status`\n\n'
    message_index += 1

    message += f'{message_index}. Request information for a specific transaction:\n`$tx_info [transaction hash ID]`\n\n'
    message_index += 1

    message += f'{message_index}. Request the address balance:\n'
    if client.ibc_enabled:
        message += f'`$balance [{client.network_name} address] <optional:network-id>`\n'
    else:
        message += f'`$balance [address]`\n'

    return message


def get_param_value(message, param_index):
    """
    Fetch the param value from the specified message at the specified index
    """
    params = list(message.content.split()[1:])  # remove the command name
    if len(params) <= param_index:
        return ""

    return str(params[param_index]).strip()


async def get_and_validate_address_from_params(client: FaucetClient, message, param_index):
    """
    Fetch and validate the address from the specified message
    """
    address = get_param_value(message, param_index)
    if not address:
        await message.reply(f'{WARNING_EMOJI} Missing address')
        return

    address = client.fetch_bech32_address(address)
    if not address.startswith(client.address_prefix):
        await message.reply(f'{WARNING_EMOJI} Expected `{client.address_prefix}` prefix')
    else:
        client.check_address(address)
        return address


async def save_transaction_statistics(transaction: str):
    """
    Transaction strings are already comma-separated
    """
    async with aiof.open('transactions.csv', 'a') as csv_file:
        await csv_file.write(f'{transaction}\n')
        await csv_file.flush()


async def balance_request(client: FaucetClient, message):
    """
    Provide the balance for a given address
    """
    try:
        address = await get_and_validate_address_from_params(client, message, 0)
        if not address:
            return
        balances = client.get_balances(address)

        denom = client.node_denom
        network_id = get_param_value(message, 1)
        if client.ibc_enabled and network_id and network_id != client.node_chain_id:
            network_denom_list = client.fetch_network_denom_list(original_denom=True)
            network_denom = next((item for item in network_denom_list if item.network_id == network_id), None)
            if network_denom:
                denom = network_denom.original_denom
            else:
                denom = None

        if denom:
            balances = list(filter(lambda balance: balance.original_denom == denom, balances))
        else:
            balances = []

        if len(balances) == 0:
            await message.reply(f'No balance for address `{address}`')
        else:
            data = map(lambda balance: {"denom": balance.denom, "amount": balance.amount}, balances)
            await message.reply(f'Balance for address `{address}`:\n```{tabulate(data, floatfmt=",.0f")}\n```\n')

    except Exception as error:
        logging.error('Balance request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)


async def faucet_status(client: FaucetClient, message):
    """
    Provide node and faucet info
    """
    try:
        node_status = client.get_node_status()
        balances = client.get_balances(client.faucet_address)
        if node_status and balances:
            await message.reply(
                f'```\n'
                f'Node moniker:      {node_status.moniker}\n'
                f'Node last block:   {node_status.last_block}\n'
                f'Faucet address:    {client.faucet_address}\n'
                f'```')
    except Exception as error:
        logging.error('Faucet status request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)


async def transaction_info(client: FaucetClient, message):
    """
    Provide info on a specific transaction
    """
    transaction_hash = get_param_value(message, 0)
    if not transaction_hash:
        await message.reply(f'{WARNING_EMOJI} Missing transaction hash ID')
        return

    if len(transaction_hash) != 64:
        await message.reply(f'{WARNING_EMOJI} Hash ID must be 64 characters long, received `{len(transaction_hash)}`')
        return

    try:
        res = client.get_tx_info(transaction_hash)
        await message.reply(
            f'```From:    {res.sender}\n'
            f'To:      {res.receiver}\n'
            f'Amount:  {res.amount}\n'
            f'Height:  {res.height}\n```')

    except Exception as error:
        logging.error('Transaction info request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)


async def get_tokens(client: FaucetClient, message):
    """
    Return all the optional tokens for faucet
    """
    try:
        network_denom_list = client.fetch_network_denom_list()
        if len(network_denom_list) == 0:
            await message.reply(f'No available tokens')
        else:
            data = map(lambda item: {"network_id": item.network_id, "denom": item.denom}, network_denom_list)
            await message.reply(f'```{tabulate(data, headers="keys")}```')

    except Exception as error:
        logging.error('Tokens request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)
        return


def on_time_blocked(client: FaucetClient, network_id: str, requester: str, message_timestamp):
    """
    Returns True, None if the given requester are not time-blocked for the specified network
    Returns False, reply if either of them is still on time-out; msg is the reply to the requester
    """
    if requester in ACTIVE_REQUESTS[client.key][network_id]:
        request = ACTIVE_REQUESTS[client.key][network_id][requester]
        check_time = request['check_time']
        requests_count = request['requests_count']
        token_requests_cap = client.get_token_requests_cap(network_id)

        if check_time > message_timestamp and requests_count >= token_requests_cap:
            seconds_left = check_time - message_timestamp
            minutes_left = seconds_left / 60
            if minutes_left > 120:
                wait_time = str(int(minutes_left / 60)) + ' hours'
            else:
                wait_time = str(int(minutes_left)) + ' minutes'
            timeout_in_hours = int(client.request_timeout / 60 / 60)

            how_many = 'once'
            if token_requests_cap == 2:
                how_many = 'twice'
            elif token_requests_cap > 2:
                how_many = f'{token_requests_cap} times'
            reply = f'{REJECT_EMOJI} You can request `{network_id}` tokens no more than {how_many} every ' \
                    f'{timeout_in_hours} hours, please try again in {wait_time}'
            return False, reply

        if check_time > message_timestamp:
            request['requests_count'] += 1
        else:
            del ACTIVE_REQUESTS[client.key][network_id][requester]

    return True, None


def check_time_limits(client: FaucetClient, network_id: str, requester: str, address: str):
    """
    Returns True, None if the given requester and address are not time-blocked for the specified network
    Returns False, reply if either of them is still on time-out; msg is the reply to the requester
    """
    message_timestamp = time.time()
    approved, reply = on_time_blocked(client, network_id, requester, message_timestamp)
    if not approved:
        return approved, reply

    approved, reply = on_time_blocked(client, network_id, address, message_timestamp)
    if not approved:
        return approved, reply

    if requester not in ACTIVE_REQUESTS[client.key][network_id] and address not in ACTIVE_REQUESTS[client.key][
        network_id]:
        ACTIVE_REQUESTS[client.key][network_id][requester] = \
            {"check_time": message_timestamp + client.request_timeout, "requests_count": 1}
        ACTIVE_REQUESTS[client.key][network_id][address] = \
            {"check_time": message_timestamp + client.request_timeout, "requests_count": 1}

    return True, None


def check_daily_cap(client: FaucetClient, network_id: str):
    """
    Returns True if the faucet has not reached the daily cap for the specified network
    Returns False otherwise
    """
    delta = client.get_amount_to_send(network_id)
    today = datetime.datetime.today().date()
    network_day_tally = NETWORKS_DAY_TALLY[client.key].get(network_id, None)
    if not network_day_tally or today != network_day_tally['active_day']:
        # The date has changed, reset the tally
        NETWORKS_DAY_TALLY[client.key][network_id] = {'active_day': today, "day_tally": delta}
        return True

    # Check tally
    daily_cap = client.get_daily_cap(network_id)
    if network_day_tally['day_tally'] + delta > daily_cap:
        return False

    network_day_tally['day_tally'] += delta
    return True


def revert_daily_consume(client: FaucetClient, network_id: str):
    network_day_tally = NETWORKS_DAY_TALLY[client.key].get(network_id, None)
    if network_day_tally:
        delta = client.get_amount_to_send(network_id)
        network_day_tally['day_tally'] -= delta


async def token_request(client: FaucetClient, message):
    """
    Send tokens to the specified address
    """
    try:
        requester = message.author
        address = await get_and_validate_address_from_params(client, message, 0)
        if not address:
            return

        network_id = get_param_value(message, 1)
        if not network_id:
            network_id = client.node_chain_id

        if network_id not in ACTIVE_REQUESTS[client.key]:
            ACTIVE_REQUESTS[client.key][network_id] = {}

        network_denom_list = client.fetch_network_denom_list(original_denom=True)
        network_denom = next((item for item in network_denom_list if item.network_id == network_id), None)
        if not network_denom:
            logging.info('%s requested $s tokens for %s but the faucet has no balance for this token',
                         requester, network_id, address)
            await message.reply(f'The faucet has no balance for `{network_id}` tokens')
            return

        # Check whether the faucet has reached the daily cap
        if not check_daily_cap(client, network_id):
            logging.info('%s requested $s tokens for %s but the daily cap has been reached',
                         requester, network_id, address)
            await message.reply("Sorry, the daily cap for this faucet has been reached")
            return

    except Exception as error:
        logging.error('Token request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)
        return

    core_team_role = discord.utils.get(requester.guild.roles, id=CORE_TEAM_ROLE_ID)
    is_core_team = core_team_role in requester.roles

    try:
        # Check whether user or address have received tokens on this testnet
        approved, reply = is_core_team, ''
        if not approved:
            approved, reply = check_time_limits(client, network_id, requester.id, address)
        if not approved:
            revert_daily_consume(client, network_id)
            logging.info('%s requested %s tokens for %s and was rejected', requester, network_id, address)
            await message.reply(reply)
            return

        # Make client call and send the response back
        original_denom = network_denom.original_denom or network_denom.denom
        amount_to_send = client.get_amount_to_send(network_id)
        amount = f'{amount_to_send}{original_denom}'
        transfer = client.tx_send(client.faucet_address, address, amount, client.tx_fees)
        logging.info('%s requested %s tokens for %s', requester, network_id, address)
        now = datetime.datetime.now()

        if client.block_explorer_tx:
            await message.reply(f'{APPROVE_EMOJI}  <{client.block_explorer_tx}{transfer}>')
        else:
            await message.reply(
                f'{APPROVE_EMOJI} Your tx is approved. To view your tx status, type `$tx_info {transfer}`')

        # Get faucet balances and save to transaction log
        balances = client.get_balances(client.faucet_address)
        await save_transaction_statistics(
            f'{now.isoformat(timespec="seconds")},'
            f'{network_id},{address},'
            f'{amount_to_send}{network_denom.original_denom},'
            f'{transfer},'
            f'{balances}')
    except Exception as error:
        if not is_core_team:
            del ACTIVE_REQUESTS[client.key][network_id][requester.id]
            del ACTIVE_REQUESTS[client.key][network_id][address]
            revert_daily_consume(client, network_id)
        logging.error('Token request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)


@discord_client.event
async def on_ready():
    """
    Gets called when the Discord client logs in
    """
    logging.info('Logged into Discord as %s', discord_client.user)


@discord_client.event
async def on_message(message):
    """
    Responds to messages on specified channels.
    """
    # Do not listen to your own messages
    if not message.content.startswith('$') or message.author == discord_client.user:
        return

    for client in CLIENTS:
        # Every client listen in specific channels
        if message.channel.name not in client.channels_to_listen:
            continue

        if message.content.startswith('$balance'):
            await balance_request(client, message)
        elif message.content.startswith('$faucet_status'):
            await faucet_status(client, message)
        elif message.content.startswith('$tx_info'):
            await transaction_info(client, message)
        elif message.content.startswith('$faucet_tokens'):
            await get_tokens(client, message)
        elif message.content.startswith('$request '):
            await token_request(client, message)
        else:
            await message.reply(get_help_message(client))

    # try:
    #     # substrate = SubstrateInterface(url="wss://kate.avail.tools/ws")
    #     # result = substrate.query('System', 'Account', ['5G6QN8usC4zDwcjTFCEngeuxcuoWEyxt71GWFUcfVZ3WH9Ln'])
    #     # balance = result.value['data']['free'] / 10 ** 18
    #     # print(3333, balance)
    #     #
    #     # call = substrate.compose_call(
    #     #     call_module='Balances',
    #     #     call_function='transfer',
    #     #     call_params={
    #     #         'dest': '5FsNCrwCAoprtpJdX6zD9PfHz6refPpkf77LytS4dLoa7WeV',
    #     #         'value': 10
    #     #     }
    #     # )
    #     #
    #     # keypair = Keypair.create_from_uri('//Alice')
    #     # extrinsic = substrate.create_signed_extrinsic(call=call, keypair=keypair)
    #     #
    #     # receipt = substrate.submit_extrinsic(extrinsic, wait_for_inclusion=True)
    #     #
    #     # print(f"Extrinsic '{receipt.extrinsic_hash}' sent and included in block '{receipt.block_hash}'")
    #
    # except Exception as error:
    #     print(44444, error)


discord_client.run(DISCORD_TOKEN)
