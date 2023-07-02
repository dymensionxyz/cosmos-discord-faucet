"""
Sets up a Discord bot to provide info and tokens

"""

# import configparser
import time
import datetime
import logging
import sys
from tabulate import tabulate
import aiofiles as aiof
import toml
import discord
import os
import re
import dymension_calls as dymension

# Turn Down Discord Logging
disc_log = logging.getLogger('discord')
disc_log.setLevel(logging.INFO)

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# Load config
config = toml.load('config.toml')

try:
    REQUEST_TIMEOUT = int(config['discord']['request_timeout'])
    DISCORD_TOKEN = os.environ['DISCORD_TOKEN']
    FAUCET_ADDRESS = config['faucet_address']
    ADDRESS_PREFIX = config['address_prefix']
    NETWORK_NAME = config['network_name']
    AMOUNT_TO_SEND = int(config['amount_to_send'])
    AMOUNT_TO_SEND_EVM = int(config['amount_to_send_evm'])
    DAILY_CAP = int(config['daily_cap'])
    TOKEN_REQUESTS_CAP = int(config['token_requests_cap'])
    HUB_CHAIN_ID = config['hub_chain_id']
    HUB_TOKEN_REQUESTS_CAP = int(config['hub_token_requests_cap'])
    DAILY_CAP_EVM = int(config['daily_cap_evm'])
    TX_FEES = int(config['tx_fees'])
    BLOCK_EXPLORER_TX = config['block_explorer_tx']
    LISTENING_CHANNELS = list(config['discord']['channels_to_listen'].split(','))
except KeyError as key:
    logging.critical('Key could not be found: %s', key)
    sys.exit()

ACTIVE_REQUESTS = {}
NETWORKS_DAY_TALLY = {}

APPROVE_EMOJI = '✅'
REJECT_EMOJI = '🚫'
WARNING_EMOJI = '❗'
GENERIC_ERROR_MESSAGE = f'{WARNING_EMOJI} dymension could not handle your request'

help_msg = '**List of available commands:**\n' \
           '1. Request tokens through the faucet:\n' \
           f'`$request [dymension address] [network-id]`\n\n' \
           '2. Request the faucet and node status:\n' \
           f'`$faucet_status`\n\n' \
           '3. Request the dymension faucet address: \n' \
           f'`$faucet_address`\n\n' \
           '4. Request information for a specific transaction:\n' \
           f'`$tx_info [transaction hash ID]`\n\n' \
           '5. Request the address balance:\n' \
           f'`$balances [dymension address]`\n\n' \
           '6. Request all the optional networks:\n' \
           f'`$request_networks`\n'

intents = discord.Intents.all()
client = discord.Client(intents=intents)


def get_param_value(message, param_index):
    """
    Fetch the param value from the specified message at the specified index
    """
    params = list(message.content.split()[1:])  # remove the command name
    if len(params) <= param_index:
        return ""

    return str(params[param_index]).strip()


async def get_and_validate_address_from_params(message, param_index):
    """
    Fetch and validate the address from the specified message
    """
    address = get_param_value(message, param_index)

    if not address:
        await message.reply(f'{WARNING_EMOJI} Missing address')
    elif not address.startswith(ADDRESS_PREFIX):
        await message.reply(f'{WARNING_EMOJI} Expected `{ADDRESS_PREFIX}` prefix')
    else:
        dymension.check_address(address)
        return address


async def get_and_validate_network_id_from_params(message, param_index):
    """
    Fetch and validate the network_id from the specified message
    """
    network_id = get_param_value(message, param_index)

    if not network_id:
        await message.reply(f'{WARNING_EMOJI} Missing network ID')
    else:
        return network_id


def get_token_requests_cap(network_id):
    if network_id == HUB_CHAIN_ID:
        return HUB_TOKEN_REQUESTS_CAP
    return TOKEN_REQUESTS_CAP


async def save_transaction_statistics(transaction: str):
    """
    Transaction strings are already comma-separated
    """
    async with aiof.open('transactions.csv', 'a') as csv_file:
        await csv_file.write(f'{transaction}\n')
        await csv_file.flush()


async def balances_request(message):
    """
    Provide the balances for a given address
    """
    try:
        address = await get_and_validate_address_from_params(message, 0)
        if not address:
            return
        balances = dymension.get_balances(address)
        if len(balances) == 0:
            await message.reply(f'No balances for address `{address}`')
        else:
            await message.reply(f'Balance for address `{address}`:\n```{tabulate(balances, floatfmt=",.0f")}\n```\n')

    except Exception as error:
        logging.error('Balance request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)


async def faucet_status(message):
    """
    Provide node and faucet info
    """
    try:
        node_status = dymension.get_node_status()
        balances = dymension.get_balances(FAUCET_ADDRESS)
        if node_status.keys() and balances:
            await message.reply(
                f'```\n'
                f'Node moniker:      {node_status["moniker"]}\n'
                f'Node last block:   {node_status["last_block"]}\n'
                f'Faucet address:    {FAUCET_ADDRESS}\n'
                f'```')
    except Exception as error:
        logging.error('Faucet status request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)


async def transaction_info(message):
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
        res = dymension.get_tx_info(transaction_hash)
        await message.reply(
            f'```From:    {res["sender"]}\n'
            f'To:      {res["receiver"]}\n'
            f'Amount:  {res["amount"]}\n'
            f'Height:  {res["height"]}\n```')

    except Exception as error:
        logging.error('Transaction info request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)


def on_time_blocked(network_id: str, requester: str, message_timestamp):
    """
    Returns True, None if the given requester are not time-blocked for the specified network
    Returns False, reply if either of them is still on time-out; msg is the reply to the requester
    """
    if requester in ACTIVE_REQUESTS[network_id]:
        request = ACTIVE_REQUESTS[network_id][requester]
        check_time = request['check_time']
        requests_count = request['requests_count']
        token_requests_cap = get_token_requests_cap(network_id)

        if check_time > message_timestamp and requests_count >= token_requests_cap:
            seconds_left = check_time - message_timestamp
            minutes_left = seconds_left / 60
            if minutes_left > 120:
                wait_time = str(int(minutes_left / 60)) + ' hours'
            else:
                wait_time = str(int(minutes_left)) + ' minutes'
            timeout_in_hours = int(REQUEST_TIMEOUT / 60 / 60)

            how_many = 'once'
            if token_requests_cap == 2:
                how_many = 'twice'
            elif token_requests_cap > 2:
                how_many = f'{token_requests_cap} times'
            reply = f'{REJECT_EMOJI} You can request coins no more than {how_many} every ' \
                    f'{timeout_in_hours} hours, please try again in {wait_time}'
            return False, reply

        if check_time > message_timestamp:
            request['requests_count'] += 1
        else:
            del ACTIVE_REQUESTS[network_id][requester]

    return True, None


def check_time_limits(network_id: str, requester: str, address: str):
    """
    Returns True, None if the given requester and address are not time-blocked for the specified network
    Returns False, reply if either of them is still on time-out; msg is the reply to the requester
    """
    message_timestamp = time.time()
    approved, reply = on_time_blocked(network_id, requester, message_timestamp)
    if not approved:
        return approved, reply

    approved, reply = on_time_blocked(network_id, address, message_timestamp)
    if not approved:
        return approved, reply

    if requester not in ACTIVE_REQUESTS[network_id] and address not in ACTIVE_REQUESTS[network_id]:
        ACTIVE_REQUESTS[network_id][requester] = \
            {"check_time": message_timestamp + REQUEST_TIMEOUT, "requests_count": 1}
        ACTIVE_REQUESTS[network_id][address] = \
            {"check_time": message_timestamp + REQUEST_TIMEOUT, "requests_count": 1}

    return True, None


def is_evm_network(network_id: str):
    """
    Returns whether the specified network is evm related
    """
    return HUB_CHAIN_ID != network_id and bool(re.search("^[^_-]+_[0-9]+[_-][0-9]+$", network_id))


def get_amount_to_send(network_id: str):
    """
    Returns the amount_to_send according to the specified network
    """
    if is_evm_network(network_id):
        return AMOUNT_TO_SEND_EVM
    return AMOUNT_TO_SEND


def get_daily_cap(network_id: str):
    """
    Returns the daily_cap according to the specified network
    """
    if is_evm_network(network_id):
        return DAILY_CAP_EVM
    return DAILY_CAP


def check_daily_cap(network_id: str):
    """
    Returns True if the faucet has not reached the daily cap for the specified network
    Returns False otherwise
    """
    delta = get_amount_to_send(network_id)
    today = datetime.datetime.today().date()
    network_day_tally = NETWORKS_DAY_TALLY.get(network_id, None)
    if not network_day_tally or today != network_day_tally['active_day']:
        # The date has changed, reset the tally
        NETWORKS_DAY_TALLY[network_id] = {'active_day': today, "day_tally": delta}
        return True

    # Check tally
    daily_cap = get_daily_cap(network_id)
    if network_day_tally['day_tally'] + delta > daily_cap:
        return False

    network_day_tally['day_tally'] += delta
    return True


def revert_daily_consume(network_id: str):
    network_day_tally = NETWORKS_DAY_TALLY.get(network_id, None)
    if network_day_tally:
        delta = get_amount_to_send(network_id)
        network_day_tally['day_tally'] -= delta


async def get_networks(message):
    """
    Return all the optional networks for faucet
    """
    try:
        network_denom_list = dymension.fetch_network_denom_list()
        if len(network_denom_list) == 0:
            await message.reply(f'No available networks')
        else:
            await message.reply(f'```{tabulate(network_denom_list, headers="keys")}```')

    except Exception as error:
        logging.error('Networks request failed: %s', error)
        await message.reply(GENERIC_ERROR_MESSAGE)
        return


async def token_request(message):
    """
    Send tokens to the specified address
    """
    try:
        requester = message.author
        address = await get_and_validate_address_from_params(message, 0)
        network_id = await get_and_validate_network_id_from_params(message, 1)
        if not address or not network_id:
            return

        if network_id not in ACTIVE_REQUESTS:
            ACTIVE_REQUESTS[network_id] = {}

        network_denom_list = dymension.fetch_network_denom_list(original_denom=True)
        network_denom = next((item for item in network_denom_list if item['network_id'] == network_id), None)
        if not network_denom:
            logging.info('%s requested $s tokens for %s but the network not supported by the faucet',
                         requester, network_id, address)
            await message.reply(f'Network `{network_id}` is not supported by the faucet')
            return

        # Check whether the faucet has reached the daily cap
        if not check_daily_cap(network_id):
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
        approved, reply = check_time_limits(network_id, requester.id, address)
        if not approved:
            revert_daily_consume(network_id)
            logging.info('%s requested %s tokens for %s and was rejected', requester, network_id, address)
            await message.reply(reply)
            return

        # Make dymension call and send the response back
        original_denom = network_denom.get('original_denom', network_denom['denom'])
        amount_to_send = get_amount_to_send(network_id)
        amount = f'{amount_to_send}{original_denom}'
        transfer = dymension.tx_send(FAUCET_ADDRESS, address, amount, TX_FEES)
        logging.info('%s requested %s tokens for %s', requester, network_id, address)
        now = datetime.datetime.now()

        if BLOCK_EXPLORER_TX:
            await message.reply(f'{APPROVE_EMOJI}  <{BLOCK_EXPLORER_TX}{transfer}>')
        else:
            await message.reply(
                f'{APPROVE_EMOJI} Your tx is approved. To view your tx status, type `$tx_info {transfer}`')

        # Get faucet balances and save to transaction log
        balances = dymension.get_balances(FAUCET_ADDRESS)
        await save_transaction_statistics(
            f'{now.isoformat(timespec="seconds")},'
            f'{network_id},{address},'
            f'{amount_to_send}{network_denom["denom"]},'
            f'{transfer},'
            f'{balances}')
    except Exception as error:
        del ACTIVE_REQUESTS[network_id][requester.id]
        del ACTIVE_REQUESTS[network_id][address]
        revert_daily_consume(network_id)
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
    # Only listen in specific channels, and do not listen to your own messages
    if not message.content.startswith('$') or \
            (message.channel.name not in LISTENING_CHANNELS) or \
            (message.author == client.user):
        return

    # Notify users of vega shutdown
    if 'vega' in message.content.lower():
        await message.reply('The Vega testnet is no longer active as of April 14, 2022. Please use Theta instead.')
        return

    if message.content.startswith('$faucet_address'):
        await message.reply(FAUCET_ADDRESS)
    elif message.content.startswith('$balances'):
        await balances_request(message)
    elif message.content.startswith('$faucet_status'):
        await faucet_status(message)
    elif message.content.startswith('$tx_info'):
        await transaction_info(message)
    elif message.content.startswith('$request_networks'):
        await get_networks(message)
    elif message.content.startswith('$request '):
        await token_request(message)
    else:
        await message.reply(help_msg)


client.run(DISCORD_TOKEN)
