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
    def __init__(self, comment, message=None):
        super().__init__(message)
        self.comment = comment

class HandleNotFoundError(TrueApiError):
    def __init__(self, comment, handle, resource):
        super().__init__(comment, f'Handle `{handle}` not found{" on `"+str(resource)+"`" if resource!=None else "."}')
        self.handle = handle

class CallLimitExceededError(TrueApiError):
    def __init__(self, comment):
        super().__init__(comment, 'Clist API call limit exceeded')

def ratelimit(f):
    tries = 3
    per_second = 3
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
            delay = next_valid - now
            if delay > 0:
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
async def _query_clist_api(path, data=None):
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
        resp = resp.json()
        return resp['objects']
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

class account:
    @staticmethod
    async def info(handle, resource):
        params = {'total_count': True, 'handle':handle} 
        if resource!=None:
            params['resource'] = resource
        resp = await _query_clist_api('account', params)
        if len(resp)==0:
            comment = 'Handle `'+str(handle)+'` not found on '+str(resource)
            raise HandleNotFoundError(comment=comment, handle=handle, resource=resource) 
        return resp