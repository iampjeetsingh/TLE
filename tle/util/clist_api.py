import logging
import os
import datetime as dt
import requests
import json

from tle import constants
from discord.ext import commands

from pathlib import Path
import functools
import time
import asyncio
from urllib.parse import urlencode
from collections import namedtuple, deque

logger = logging.getLogger(__name__)
URL_BASE = 'https://clist.by/api/v2/'
_CLIST_API_TIME_DIFFERENCE = 30 * 60  # seconds


class ClistApiError(commands.CommandError):
    """Base class for all API related errors."""

    def __init__(self, message=None):
        super().__init__(message or 'Clist API error')


class ClientError(ClistApiError):
    """An error caused by a request to the API failing."""

    def __init__(self):
        super().__init__('Error connecting to Clist API')

class TrueApiError(ClistApiError):
    """An error originating from a valid response of the API."""
    def __init__(self, comment=None, message=None):
        super().__init__(message)
        self.comment = comment

class HandleNotFoundError(TrueApiError):
    def __init__(self, handle, resource=None):
        super().__init__(message=f'Handle `{handle}` not found{" on `"+str(resource)+"`" if resource!=None else "."}')
        self.handle = handle

class CallLimitExceededError(TrueApiError):
    def __init__(self, comment=None):
        super().__init__(message='Clist API call limit exceeded')
        self.comment = comment

def ratelimit(f):
    tries = 5
    per_second = 1
    last = deque([0]*per_second)

    @functools.wraps(f)
    async def wrapped(*args, **kwargs):
        for i in range(tries):
            now = time.time()

            # Next valid slot is 1s after the `per_second`th last request
            next_valid = max(now, 1 + last[0])
            last.append(next_valid)
            last.popleft()

            # Delay as needed
            delay = 15
            if i > 0:
                await asyncio.sleep(delay)

            try:
                return await f(*args, **kwargs)
            except (ClientError, CallLimitExceededError, ClistApiError) as e:
                logger.info(f'Try {i+1}/{tries} at query failed.')
                logger.info(repr(e))
                if i < tries - 1:
                    logger.info(f'Retrying...')
                else:
                    logger.info(f'Aborting.')
                    raise e
    return wrapped


@ratelimit
async def _query_clist_api(path, data={}):
    url = URL_BASE + path
    clist_token = os.getenv('CLIST_API_TOKEN')
    url += '?'+ str(urlencode(data))
    print("Calling Clist : "+url)
    url+='&'+clist_token
    try:
        resp = requests.get(url)
        if resp.status_code != 200:
            if resp.status_code == 429:
                raise CallLimitExceededError
            else:
                raise ClistApiError
        return resp.json()
    except Exception as e:
        logger.error(f'Request to Clist API encountered error: {e!r}')
        raise ClientError from e


def _query_api():
    clist_token = os.getenv('CLIST_API_TOKEN')
    contests_start_time = dt.datetime.utcnow() - dt.timedelta(days=2)
    contests_start_time_string = contests_start_time.strftime(
        "%Y-%m-%dT%H%%3A%M%%3A%S")
    url = URL_BASE +'/contest?limit=200&start__gte=' + \
        contests_start_time_string + '&' + clist_token

    try:
        resp = requests.get(url)
        if resp.status_code != 200:
            raise ClistApiError
        return resp.json()['objects']
    except Exception as e:
        logger.error(f'Request to Clist API encountered error: {e!r}')
        raise ClientError from e


def cache(forced=False):
    
    current_time_stamp = dt.datetime.utcnow().timestamp()
    db_file = Path(constants.CONTESTS_DB_FILE_PATH)

    db = None
    try:
        with db_file.open() as f:
            db = json.load(f)
    except BaseException:
        pass

    last_time_stamp = db['querytime'] if db and db['querytime'] else 0

    if not forced and current_time_stamp - \
            last_time_stamp < _CLIST_API_TIME_DIFFERENCE:
        return

    contests = _query_api()
    db = {}
    db['querytime'] = current_time_stamp
    db['objects'] = contests
    with open(db_file, 'w') as f:
        json.dump(db, f)

async def account(handle, resource):
    params = {'total_count': True, 'handle':handle} 
    if resource!=None:
        params['resource'] = resource
    resp = await _query_clist_api('account', params)
    if resp==None or 'objects' not in resp:
        raise ClientError
    else:
        resp = resp['objects']
    if len(resp)==0:
        comment = 'Handle `'+str(handle)+'` not found on '+str(resource)
        raise HandleNotFoundError(comment=comment, handle=handle, resource=resource) 
    return resp

async def statistics(account_id=None, contest_id=None, order_by=None):
    params = {'limit':1000}
    if account_id!=None: params['account_id'] = account_id
    if contest_id!=None: params['contest_id'] = contest_id
    if order_by!=None: params['order_by'] = order_by;
    resp = await _query_clist_api('statistics', params)
    if resp==None or 'objects' not in resp:
        raise ClientError
    else:
        resp = resp['objects']
    return resp

async def contest(contest_id):
    resp = await _query_clist_api('contest/'+str(contest_id))
    return resp

async def fetch_user_info(resource, handles):
    regex = '|'.join(handles)
    params = {'resource':resource, 'handle__regex':regex, 'limit':1000}
    resp = await _query_clist_api('account', params)
    if resp==None or 'objects' not in resp:
        raise ClientError
    else:
        resp = resp['objects']
    return resp