import logging
import os
import datetime as dt
from tle.util.codeforces_api import RatingChange, make_from_dict, Contest as CfContest
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
    tries = 3
    @functools.wraps(f)
    async def wrapped(*args, **kwargs):
        for i in range(tries):
            try:
                return await f(*args, **kwargs)
            except (CallLimitExceededError) as e:
                delay = 20
                await asyncio.sleep(delay*(i+1))
                logger.info(f'Try {i+1}/{tries} at query failed.')
                if i < tries - 1:
                    logger.info(f'Retrying...')
                else:
                    logger.info(f'Aborting.')
                    raise e
            except (ClientError, ClistApiError) as e:
                logger.info(f'Try {i+1}/{tries} at query failed.')
                if i < tries - 1:
                    logger.info(f'Retrying...')
                else:
                    logger.info(f'Aborting.')
                    raise e
    return wrapped


@ratelimit
async def _query_clist_api(path, data):
    url = URL_BASE + path
    clist_token = os.getenv('CLIST_API_TOKEN')
    if data is None:
        url += '?'+clist_token
    else:
        url += '?'+ str(urlencode(data))
        url+='&'+clist_token
    print("Calling Clist : "+url)
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
        raise HandleNotFoundError(handle=handle, resource=resource) 
    return resp

async def statistics(account_id=None, contest_id=None, order_by=None, account_ids=None, resource=None, with_problems=False, with_extra_fields=False, limit=1000):
    params = {'limit':limit}
    if account_id!=None: params['account_id'] = account_id
    if contest_id!=None: params['contest_id'] = contest_id
    if order_by!=None: params['order_by'] = order_by
    if with_problems: params['with_problems'] = True
    if with_extra_fields: params['with_more_fields'] = True
    if account_ids!=None:
        ids = ""
        for i in range(len(account_ids)):
            ids += str(account_ids[i])
            if i!=(len(account_ids)-1):
                ids += ','
        params['account_id__in']=ids
    if resource!=None: params['resource'] = resource
    results = []
    offset = 0
    while True:
        params['offset'] = offset
        resp = await _query_clist_api('statistics', params)
        if resp==None or 'objects' not in resp:
            if offset==0:
                raise ClientError
            else:
                break
        else:
            objects = resp['objects']
            results += objects
            if(len(objects)<1000):
                break
        offset+=1000
    return results

class Contest(CfContest):
    @property
    def resource(self):
        return self._resource

    @resource.setter
    def resource(self, value):
        self._resource = value

    @property
    def url(self):
        return self._url

    @url.setter
    def url(self, value):
        self._url = value
    
    @property
    def register_url(self):
        return self._url

def time_in_seconds(time_str):
    time = dt.datetime.strptime(time_str,'%Y-%m-%dT%H:%M:%S')
    return int((time-dt.datetime(1970,1,1)).total_seconds())

def format_contest(contest):
    start = time_in_seconds(contest['start'])
    now = int(time.time())
    duration = contest['duration']
    phase = ''
    if now<start:
        phase = 'BEFORE'
    elif now<start+duration:
        phase = 'CODING'
    else:
        phase = 'FINISHED'
    contest_dict = {
        'id': contest['id'],
        'name': contest['event'] ,
        'startTimeSeconds': start ,
        'durationSeconds': duration ,
        'type': 'CLIST' ,
        'phase': phase ,
        'preparedBy': None
    }
    res = make_from_dict(Contest, contest_dict)
    res.resource = contest['resource']
    res.url = contest['href']
    return res

async def contest(contest_id, with_problems=False):
    params = {'id':contest_id}
    if with_problems:
        params['with_problems'] = True
    resp = await _query_clist_api('contest', params)
    if resp==None or 'objects' not in resp:
        raise ClientError
    else:
        resp = resp['objects']
    return resp[0]

async def is_contest_parsed(contest_id):
    resp = await statistics(contest_id=contest_id, limit=10, order_by='place')
    return len(resp)!=0

async def search_contest(regex=None, date_limits=None, resource=None, with_problems=False, order_by=None):
    params = {'limit':1000}
    if resource!=None:
        params['resource'] = resource
    if regex!=None:
        params['event__regex'] = regex
    if date_limits!=None:
        if date_limits[0]:
            params['start__gte'] = date_limits[0]
        if date_limits[1]:
            params['start__lt'] = date_limits[1]
    if with_problems:
        params['with_problems'] = True
    if order_by!=None: 
        params['order_by'] = order_by
    resp = await _query_clist_api('contest', data=params)
    if resp==None or 'objects' not in resp:
        raise ClientError
    else:
        resp = resp['objects']
    return resp

async def fetch_user_info(resource, account_ids=None, handles=None):
    if account_ids is None and handles is None:
        return []
    params = {'resource':resource, 'limit':1000}
    if account_ids!=None:
        ids = ""
        for i in range(len(account_ids)):
            ids += str(account_ids[i])
            if i!=(len(account_ids)-1):
                ids += ','
        params['id__in']=ids
    if handles!=None:
        regex = '$|^'.join(handles)
        params['handle__regex'] = '^'+regex+'$'
    resp = await _query_clist_api('account', params)
    if resp==None or 'objects' not in resp:
        raise ClientError
    else:
        resp = resp['objects']
    return resp

async def fetch_rating_changes(account_ids=None, performance=False):
    resp = await statistics(account_ids=account_ids, order_by='date', with_extra_fields=performance)
    result = []
    for changes in resp:
        time = dt.datetime.strptime(changes['date'],'%Y-%m-%dT%H:%M:%S')
        if changes['new_rating']==None: continue
        rating_change = changes['rating_change'] if changes['rating_change']!=None else 0
        old_rating = changes['old_rating'] if changes['old_rating']!=None else changes['new_rating']-rating_change
        if performance:
            if 'more_fields' not in changes or 'performance' not in changes['more_fields']:
                old_rating = None
            else:
                old_rating = changes['more_fields']['performance']
        if not old_rating: continue
        ratingchangedict = {
            'contestId':changes['contest_id'], 
            'contestName':changes['event'], 
            'handle':changes['handle'], 
            'rank':changes['place'], 
            'ratingUpdateTimeSeconds':int((time-dt.datetime(1970,1,1)).total_seconds()), 
            'oldRating':old_rating, 
            'newRating':changes['new_rating']
        }
        result.append(make_from_dict(RatingChange, ratingchangedict))
    return result