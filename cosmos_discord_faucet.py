"""
Sets up a Discord bot to provide info and tokens

"""
import time
import datetime
import logging
import sys
from tabulate import tabulate
import aiofiles as aiof
import toml
import discord
import os
import dymension_calls as dymension
from faucet_types import FaucetEnv

# Turn Down Discord Logging
disc_log = logging.getLogger('discord')
disc_log.setLevel(logging.INFO)

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# Load config
config = toml.load('config.toml')
envs = config['envs']

try:
    ENVS = list(map(lambda env: FaucetEnv(env, **envs[env]), envs))
    DISCORD_TOKEN = os.environ['DISCORD_TOKEN']
    ACTIVE_REQUESTS = {env: {} for env in envs}
    NETWORKS_DAY_TALLY = {env: {} for env in envs}
except KeyError as key:
    logging.critical('Key could not be found: %s', key)
    sys.exit()

APPROVE_EMOJI = '✅'
REJECT_EMOJI = '🚫'
WARNING_EMOJI = '❗'
GENERIC_ERROR_MESSAGE = f'{WARNING_EMOJI} dymension could not handle your request'

intents = discord.Intents.all()
client = discord.Client(intents=intents)


def get_help_message(env: FaucetEnv):
    message = '**List of available commands:**\n'
    message_index = 1

    if env.ibc_enabled:
        message += f'{message_index}. Lists all tokens available in the faucet:\n`$faucet_tokens`\n\n'
        message_index += 1

    message += f'{message_index}. Request tokens through the faucet:\n'
    if env.ibc_enabled:
        message += f'`$request [{env.network_name} address] <optional:network-id>`\n\n'
    else:
        message += f'`$request [address]`\n\n'
    message_index += 1

    message += f'{message_index}. Request the faucet and node status:\n`$faucet_status`\n\n'
    message_index += 1

    message += f'{message_index}. Request information for a specific transaction:\n`$tx_info [transaction hash ID]`\n\n'
    message_index += 1

    message += f'{message_index}. Request the address balance:\n'
    if env.ibc_enabled:
        message += f'`$balance [{env.network_name} address] <optional:network-id>`\n'
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


async def get_and_validate_address_from_params(env: FaucetEnv, message, param_index):
    """
    Fetch and validate the address from the specified message
    """
    address = get_param_value(message, param_index)

    if not address:
        await message.reply(f'{WARNING_EMOJI} Missing address')
    elif not address.startswith(env.address_prefix):
        await message.reply(f'{WARNING_EMOJI} Expected `{env.address_prefix}` prefix')
    else:
        dymension.check_address(env, address)
        return address


async def save_transaction_statistics(transaction: str):
    """
    Transaction strings are already comma-separated
    """
    async with aiof.open('transactions.csv', 'a') as csv_file:
        await csv_file.write(f'{transaction}\n')
        await csv_file.flush()


async def balance_request(env: FaucetEnv, message):
    """
    Provide the balance for a given address
    """
    try:
        address = await get_and_validate_address_from_params(env, message, 0)
        if not address:
            return
        balances = dymension.get_balances(env, address)

        denom = env.node_denom
        network_id = get_param_value(message, 1)
        if env.ibc_enabled and network_id and network_id != env.node_chain_id:
            network_denom_list = dymension.fetch_network_denom_list(env, original_denom=True)
            network_denom = next((item for item in network_denom_list if item['network_id'] == network_id), None)
            if network_denom:
                denom = network_denom['original_denom']
            else:
                denom = None

        if denom:
            balances = list(filter(lambda balance: balance['original_denom'] == denom, balances))
        else:
            balances = []

        if len(balances) == 0:
            await message.reply(f'No balance for address `{address}`')
        else:
            balances = [{field: entry[field] for field in ["denom", "amount"]} for entry in balances]
            await message.reply(f'Balance for address `{address}`:\n```{tabulate(balances, floatfmt=",.0f")}\n```\n')

    except Exception as error:
        logging.error('Balance request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)


async def faucet_status(env: FaucetEnv, message):
    """
    Provide node and faucet info
    """
    try:
        node_status = dymension.get_node_status(env)
        balances = dymension.get_balances(env, env.faucet_address)
        if node_status.keys() and balances:
            await message.reply(
                f'```\n'
                f'Node moniker:      {node_status["moniker"]}\n'
                f'Node last block:   {node_status["last_block"]}\n'
                f'Faucet address:    {env.faucet_address}\n'
                f'```')
    except Exception as error:
        logging.error('Faucet status request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)


async def transaction_info(env: FaucetEnv, message):
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
        res = dymension.get_tx_info(env, transaction_hash)
        await message.reply(
            f'```From:    {res["sender"]}\n'
            f'To:      {res["receiver"]}\n'
            f'Amount:  {res["amount"]}\n'
            f'Height:  {res["height"]}\n```')

    except Exception as error:
        logging.error('Transaction info request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)


async def get_tokens(env: FaucetEnv, message):
    """
    Return all the optional tokens for faucet
    """
    try:
        network_denom_list = dymension.fetch_network_denom_list(env)
        if len(network_denom_list) == 0:
            await message.reply(f'No available tokens')
        else:
            await message.reply(f'```{tabulate(network_denom_list, headers="keys")}```')

    except Exception as error:
        logging.error('Tokens request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)
        return


def on_time_blocked(env: FaucetEnv, network_id: str, requester: str, message_timestamp):
    """
    Returns True, None if the given requester are not time-blocked for the specified network
    Returns False, reply if either of them is still on time-out; msg is the reply to the requester
    """
    if requester in ACTIVE_REQUESTS[env.key][network_id]:
        request = ACTIVE_REQUESTS[env.key][network_id][requester]
        check_time = request['check_time']
        requests_count = request['requests_count']
        token_requests_cap = env.get_token_requests_cap(network_id)

        if check_time > message_timestamp and requests_count >= token_requests_cap:
            seconds_left = check_time - message_timestamp
            minutes_left = seconds_left / 60
            if minutes_left > 120:
                wait_time = str(int(minutes_left / 60)) + ' hours'
            else:
                wait_time = str(int(minutes_left)) + ' minutes'
            timeout_in_hours = int(env.request_timeout / 60 / 60)

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
            del ACTIVE_REQUESTS[env.key][network_id][requester]

    return True, None


def check_time_limits(env: FaucetEnv, network_id: str, requester: str, address: str):
    """
    Returns True, None if the given requester and address are not time-blocked for the specified network
    Returns False, reply if either of them is still on time-out; msg is the reply to the requester
    """
    message_timestamp = time.time()
    approved, reply = on_time_blocked(env, network_id, requester, message_timestamp)
    if not approved:
        return approved, reply

    approved, reply = on_time_blocked(env, network_id, address, message_timestamp)
    if not approved:
        return approved, reply

    if requester not in ACTIVE_REQUESTS[env.key][network_id] and address not in ACTIVE_REQUESTS[env.key][network_id]:
        ACTIVE_REQUESTS[env.key][network_id][requester] = \
            {"check_time": message_timestamp + env.request_timeout, "requests_count": 1}
        ACTIVE_REQUESTS[env.key][network_id][address] = \
            {"check_time": message_timestamp + env.request_timeout, "requests_count": 1}

    return True, None


def check_daily_cap(env: FaucetEnv, network_id: str):
    """
    Returns True if the faucet has not reached the daily cap for the specified network
    Returns False otherwise
    """
    delta = env.get_amount_to_send(network_id)
    today = datetime.datetime.today().date()
    network_day_tally = NETWORKS_DAY_TALLY[env.key].get(network_id, None)
    if not network_day_tally or today != network_day_tally['active_day']:
        # The date has changed, reset the tally
        NETWORKS_DAY_TALLY[env.key][network_id] = {'active_day': today, "day_tally": delta}
        return True

    # Check tally
    daily_cap = env.get_daily_cap(network_id)
    if network_day_tally['day_tally'] + delta > daily_cap:
        return False

    network_day_tally['day_tally'] += delta
    return True


def revert_daily_consume(env: FaucetEnv, network_id: str):
    network_day_tally = NETWORKS_DAY_TALLY[env.key].get(network_id, None)
    if network_day_tally:
        delta = env.get_amount_to_send(network_id)
        network_day_tally['day_tally'] -= delta


async def token_request(env: FaucetEnv, message):
    """
    Send tokens to the specified address
    """
    try:
        requester = message.author
        address = await get_and_validate_address_from_params(env, message, 0)
        if not address:
            return

        network_id = get_param_value(message, 1)
        if not network_id:
            network_id = env.node_chain_id

        if network_id not in ACTIVE_REQUESTS[env.key]:
            ACTIVE_REQUESTS[env.key][network_id] = {}

        network_denom_list = dymension.fetch_network_denom_list(env, original_denom=True)
        network_denom = next((item for item in network_denom_list if item['network_id'] == network_id), None)
        if not network_denom:
            logging.info('%s requested $s tokens for %s but the faucet has no balance for this token',
                         requester, network_id, address)
            await message.reply(f'The faucet has no balance for `{network_id}` tokens')
            return

        # Check whether the faucet has reached the daily cap
        if not check_daily_cap(env, network_id):
            logging.info('%s requested $s tokens for %s but the daily cap has been reached',
                         requester, network_id, address)
            await message.reply("Sorry, the daily cap for this faucet has been reached")
            return

    except Exception as error:
        logging.error('Token request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)
        return

    try:
        # Check whether user or address have received tokens on this testnet
        approved, reply = check_time_limits(env, network_id, requester.id, address)
        if not approved:
            revert_daily_consume(env, network_id)
            logging.info('%s requested %s tokens for %s and was rejected', requester, network_id, address)
            await message.reply(reply)
            return

        # Make dymension call and send the response back
        original_denom = network_denom.get('original_denom', network_denom['denom'])
        amount_to_send = env.get_amount_to_send(network_id)
        amount = f'{amount_to_send}{original_denom}'
        transfer = dymension.tx_send(env, env.faucet_address, address, amount, env.tx_fees)
        logging.info('%s requested %s tokens for %s', requester, network_id, address)
        now = datetime.datetime.now()

        if env.block_explorer_tx:
            await message.reply(f'{APPROVE_EMOJI}  <{env.block_explorer_tx}{transfer}>')
        else:
            await message.reply(
                f'{APPROVE_EMOJI} Your tx is approved. To view your tx status, type `$tx_info {transfer}`')

        # Get faucet balances and save to transaction log
        balances = dymension.get_balances(env, env.faucet_address)
        await save_transaction_statistics(
            f'{now.isoformat(timespec="seconds")},'
            f'{network_id},{address},'
            f'{amount_to_send}{network_denom["original_denom"]},'
            f'{transfer},'
            f'{balances}')
    except Exception as error:
        del ACTIVE_REQUESTS[env.key][network_id][requester.id]
        del ACTIVE_REQUESTS[env.key][network_id][address]
        revert_daily_consume(env, network_id)
        logging.error('Token request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)


@client.event
async def on_ready():
    """
    Gets called when the Discord client logs in
    """
    logging.info('Logged into Discord as %s', client.user)


@client.event
async def on_message(message):
    """
    Responds to messages on specified channels.
    """
    # Do not listen to your own messages
    if not message.content.startswith('$') or message.author == client.user:
        return

    for env in ENVS:
        # Every env listen in specific channels
        if message.channel.name not in env.channels_to_listen:
            continue

        if message.content.startswith('$balance'):
            await balance_request(env, message)
        elif message.content.startswith('$faucet_status'):
            await faucet_status(env, message)
        elif message.content.startswith('$tx_info'):
            await transaction_info(env, message)
        elif message.content.startswith('$faucet_tokens'):
            await get_tokens(env, message)
        elif message.content.startswith('$request '):
            await token_request(env, message)
        else:
            await message.reply(get_help_message(env))


client.run(DISCORD_TOKEN)
