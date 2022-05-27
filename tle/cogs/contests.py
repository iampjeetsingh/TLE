import asyncio
import functools
import json
import logging
import time
import datetime as dt
import pytz
from collections import defaultdict, namedtuple
from typing import List

import discord
from discord.ext import commands
from matplotlib import pyplot as plt

from tle import constants
from tle.util import codeforces_common as cf_common
from tle.util import cache_system2
from tle.util import codeforces_api as cf
from tle.util import clist_api as clist
from tle.util import db
from tle.util import discord_common
from tle.util import events
from tle.util import paginator
from tle.util import ranklist as rl
from tle.util import table
from tle.util import tasks
from tle.util import graph_common as gc

_CONTESTS_PER_PAGE = 5
_CONTEST_PAGINATE_WAIT_TIME = 5 * 60
_STANDINGS_PER_PAGE = 15
_STANDINGS_PAGINATE_WAIT_TIME = 2 * 60
_FINISHED_CONTESTS_LIMIT = 5
_WATCHING_RATED_VC_WAIT_TIME = 5 * 60  # seconds
_RATED_VC_EXTRA_TIME = 10 * 60  # seconds
_MIN_RATED_CONTESTANTS_FOR_RATED_VC = 50

_PATTERNS = {
    'abc': 'atcoder.jp',
    'arc': 'atcoder.jp',
    'agc': 'atcoder.jp',
    'kickstart': 'codingcompetitions.withgoogle.com',
    'codejam': 'codingcompetitions.withgoogle.com',
    'lunchtime': 'codechef.com',
    'long': 'codechef.com',
    'cookoff': 'codechef.com',
    'starters': 'codechef.com',
    'hackercup': 'facebook.com/hackercup'
}

def parse_date(arg):
    try:
        if len(arg) == 8:
            fmt = '%d%m%Y'
        elif len(arg) == 6:
            fmt = '%m%Y'
        elif len(arg) == 4:
            fmt = '%Y'
        else:
            raise ValueError
        return dt.datetime.strptime(arg, fmt)
    except ValueError:
        raise ContestCogError(f'{arg} is an invalid date argument')

class ContestCogError(commands.CommandError):
    pass


def _contest_start_time_format(contest, tz):
    start = dt.datetime.fromtimestamp(contest.startTimeSeconds, tz)
    tz = str(tz)
    if tz=='Asia/Kolkata':
        tz = 'IST'
    return f'{start.strftime("%d %b %y, %H:%M")} {tz}'


def _contest_duration_format(contest):
    duration_days, duration_hrs, duration_mins, _ = cf_common.time_format(contest.durationSeconds)
    duration = f'{duration_hrs}h {duration_mins}m'
    if duration_days > 0:
        duration = f'{duration_days}d ' + duration
    return duration


def _get_formatted_contest_desc(id_str, start, duration, url, max_duration_len):
    em = '\N{EN SPACE}'
    sq = '\N{WHITE SQUARE WITH UPPER RIGHT QUADRANT}'
    desc = (f'`{em}{id_str}{em}|'
            f'{em}{start}{em}|'
            f'{em}{duration.rjust(max_duration_len, em)}{em}|'
            f'{em}`[`link {sq}`]({url} "Link to contest page")')
    return desc


def _get_embed_fields_from_contests(contests):
    infos = [(contest.name, str(contest.id), _contest_start_time_format(contest, dt.timezone.utc),
              _contest_duration_format(contest), contest.register_url)
             for contest in contests]

    max_duration_len = max(len(duration) for _, _, _, duration, _ in infos)

    fields = []
    for name, id_str, start, duration, url in infos:
        value = _get_formatted_contest_desc(id_str, start, duration, url, max_duration_len)
        fields.append((name, value))
    return fields


def _get_ongoing_vc_participants():
    """ Returns a set containing the `member_id`s of users who are registered in an ongoing vc.
    """
    ongoing_vc_ids = cf_common.user_db.get_ongoing_rated_vc_ids()
    ongoing_vc_participants = set()
    for vc_id in ongoing_vc_ids:
        vc_participants = set(cf_common.user_db.get_rated_vc_user_ids(vc_id))
        ongoing_vc_participants |= vc_participants
    return ongoing_vc_participants

class Contests(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.future_contests = None
        self.active_contests = None
        self.finished_contests = None
        self.start_time_map = defaultdict(list)
        self.task_map = defaultdict(list)

        self.member_converter = commands.MemberConverter()
        self.role_converter = commands.RoleConverter()

        self.logger = logging.getLogger(self.__class__.__name__)

    @commands.Cog.listener()
    @discord_common.once
    async def on_ready(self):
        self._watch_rated_vcs_task.start()
    
    @staticmethod
    def _make_contest_pages(contests, title):
        pages = []
        chunks = paginator.chunkify(contests, _CONTESTS_PER_PAGE)
        for chunk in chunks:
            embed = discord_common.cf_color_embed()
            for name, value in _get_embed_fields_from_contests(chunk):
                embed.add_field(name=name, value=value, inline=False)
            pages.append((title, embed))
        return pages

    async def _send_contest_list(self, ctx, contests, *, title, empty_msg):
        if contests is None:
            raise ContestCogError('Contest list not present')
        if len(contests) == 0:
            await ctx.send(embed=discord_common.embed_neutral(empty_msg))
            return
        pages = self._make_contest_pages(contests, title)
        paginator.paginate(self.bot, ctx.channel, pages, wait_time=_CONTEST_PAGINATE_WAIT_TIME,
                           set_pagenum_footers=True)

    @staticmethod
    def _get_cf_or_ioi_standings_table(problem_indices, handle_standings, deltas=None, *, mode):
        assert mode in ('cf', 'ioi')

        def maybe_int(value):
            return int(value) if mode == 'cf' else value

        header_style = '{:>} {:<}    {:^}  ' + '  '.join(['{:^}'] * len(problem_indices))
        body_style = '{:>} {:<}    {:>}  ' + '  '.join(['{:>}'] * len(problem_indices))
        header = ['#', 'Handle', '='] + problem_indices
        if deltas:
            header_style += '  {:^}'
            body_style += '  {:>}'
            header += ['\N{INCREMENT}']

        body = []
        for handle, standing in handle_standings:
            virtual = '#' if standing.party.participantType == 'VIRTUAL' else ''
            tokens = [standing.rank, handle + ':' + virtual, maybe_int(standing.points)]
            for problem_result in standing.problemResults:
                score = ''
                if problem_result.points:
                    score = str(maybe_int(problem_result.points))
                tokens.append(score)
            body.append(tokens)

        if deltas:
            for tokens, delta in zip(body, deltas):
                tokens.append('' if delta is None else f'{delta:+}')
        return header_style, body_style, header, body

    @staticmethod
    def _get_icpc_standings_table(problem_indices, handle_standings, deltas=None):
        header_style = '{:>} {:<}    {:^}  {:^}  ' + '  '.join(['{:^}'] * len(problem_indices))
        body_style = '{:>} {:<}    {:>}  {:>}  ' + '  '.join(['{:<}'] * len(problem_indices))
        header = ['#', 'Handle', '=', '-'] + problem_indices
        if deltas:
            header_style += '  {:^}'
            body_style += '  {:>}'
            header += ['\N{INCREMENT}']

        body = []
        for handle, standing in handle_standings:
            virtual = '#' if standing.party.participantType == 'VIRTUAL' else ''
            tokens = [standing.rank, handle + ':' + virtual, int(standing.points), int(standing.penalty)]
            for problem_result in standing.problemResults:
                score = '+' if problem_result.points else ''
                if problem_result.rejectedAttemptCount:
                    penalty = str(problem_result.rejectedAttemptCount)
                    if problem_result.points:
                        score += penalty
                    else:
                        score = '-' + penalty
                tokens.append(score)
            body.append(tokens)

        if deltas:
            for tokens, delta in zip(body, deltas):
                tokens.append('' if delta is None else f'{delta:+}')
        return header_style, body_style, header, body

    def _make_standings_pages(self, contest, problem_indices, handle_standings, deltas=None):
        pages = []
        handle_standings_chunks = paginator.chunkify(handle_standings, _STANDINGS_PER_PAGE)
        num_chunks = len(handle_standings_chunks)
        delta_chunks = paginator.chunkify(deltas, _STANDINGS_PER_PAGE) if deltas else [None] * num_chunks

        if contest.type == 'CF':
            get_table = functools.partial(self._get_cf_or_ioi_standings_table, mode='cf')
        elif contest.type == 'ICPC':
            get_table = self._get_icpc_standings_table
        elif contest.type == 'IOI':
            get_table = functools.partial(self._get_cf_or_ioi_standings_table, mode='ioi')
        else:
            assert False, f'Unexpected contest type {contest.type}'

        num_pages = 1
        for handle_standings_chunk, delta_chunk in zip(handle_standings_chunks, delta_chunks):
            header_style, body_style, header, body = get_table(problem_indices,
                                                               handle_standings_chunk,
                                                               delta_chunk)
            t = table.Table(table.Style(header=header_style, body=body_style))
            t += table.Header(*header)
            t += table.Line('\N{EM DASH}')
            for row in body:
                t += table.Data(*row)
            t += table.Line('\N{EM DASH}')
            page_num_footer = f' # Page: {num_pages} / {num_chunks}' if num_chunks > 1 else ''

            # We use yaml to get nice colors in the ranklist.
            content = f'```yaml\n{t}\n{page_num_footer}```'
            pages.append((content, None))
            num_pages += 1

        return pages
    
    def _make_clist_standings_pages(self, standings, problemset=None, division=None):
        if standings is None or len(standings)==0:
            return "```No handles found inside ranklist```"
        show_rating_changes = False
        problems = []
        problem_indices = []
        if problemset:
            if division!=None:
                problemset = problemset['division'][division]
            for problem in problemset:
                if 'short' in problem:
                    short = problem['short']
                    if len(short)>3:
                        problem_indices = None
                    if problem_indices!=None:
                        problem_indices.append(short)
                    problems.append(short)
                elif 'code' in problem:
                    problem_indices = None
                    problems.append(problem['code'])
        for standing in standings:
            if not show_rating_changes and standing['rating_change']!=None:
                show_rating_changes = True
            if problemset is None and 'problems' in standing:
                for problem_key in standing['problems']:
                    if problem_key not in problems:
                        problems.append(problem_key)
        def maybe_int(value):
            if '.' not in str(value):
                return value
            try:
                return int(value)
            except:
                return value
        show_rating_changes = any([standing['rating_change']!=None for standing in standings])
        pages = []
        standings_chunks = paginator.chunkify(standings, _STANDINGS_PER_PAGE)
        num_chunks = len(standings_chunks)
        problem_indices = problem_indices or [chr(ord('A')+i) for i in range(len(problems))]
        header_style = '{:>} {:<}    {:^}  ' 
        body_style = '{:>} {:<}    {:>}  '
        header = ['#', 'Handle', '='] 
        header_style += '  '.join(['{:^}'] * len(problem_indices))
        body_style += '  '.join(['{:>}'] * len(problem_indices))
        header += problem_indices
        if show_rating_changes:
            header_style += '  {:^}'
            body_style += '  {:>}'
            header += ['\N{INCREMENT}']
        
        num_pages = 1
        for standings_chunk in standings_chunks:
            body = []
            for standing in standings_chunk:
                score = int(standing['score']) if standing['score'] else ' '
                problem_results = [maybe_int(standing['problems'][problem_key]['result']) 
                                            if standing.get('problems', None) and standing['problems'].get(problem_key, None) and 
                                                    standing['problems'][problem_key].get('result', None) 
                                                        else ' ' for problem_key in problems]
                tokens = [int(standing['place']), standing['handle'], maybe_int(score)]
                tokens += problem_results
                if show_rating_changes:
                    delta = int(standing['rating_change']) if standing['rating_change'] else ' '
                    if delta!=' ':
                        delta = '+'+str(delta) if delta>0 else str(delta)
                    tokens += [delta]
                body.append(tokens)
            t = table.Table(table.Style(header=header_style, body=body_style))
            t += table.Header(*header)
            t += table.Line('\N{EM DASH}')
            for row in body:
                t += table.Data(*row)
            t += table.Line('\N{EM DASH}')
            page_num_footer = f' # Page: {num_pages} / {num_chunks}' if num_chunks > 1 else ''

            # We use yaml to get nice colors in the ranklist.
            content = f'```yaml\n{t}\n{page_num_footer}```'
            pages.append((content, None))
            num_pages += 1
        return pages

    @staticmethod
    def _make_contest_embed_for_ranklist(ranklist=None, contest=None, timezone:pytz.timezone=cf_common.default_timezone, parsed_at=None):
        contest = ranklist.contest if ranklist else contest
        assert contest.phase != 'BEFORE', f'Contest {contest.id} has not started.'
        embed = discord_common.cf_color_embed(title=contest.name, url=contest.url)
        phase = contest.phase.capitalize().replace('_', ' ')
        embed.add_field(name='Phase', value=phase)
        if ranklist and ranklist.is_rated:
            embed.add_field(name='Deltas', value=ranklist.deltas_status)
        now = time.time()
        en = '\N{EN SPACE}'
        if contest.phase == 'CODING':
            elapsed = cf_common.pretty_time_format(now - contest.startTimeSeconds, shorten=True)
            remaining = cf_common.pretty_time_format(contest.end_time - now, shorten=True)
            msg = f'{elapsed} elapsed{en}|{en}{remaining} remaining'
            embed.add_field(name='Tick tock', value=msg, inline=False)
        else:
            start = _contest_start_time_format(contest, timezone)
            duration = _contest_duration_format(contest)
            since = cf_common.pretty_time_format(now - contest.end_time, only_most_significant=True)
            msg = f'{start}{en}|{en}{duration}{en}|{en}Ended {since} ago'
            embed.add_field(name='When', value=msg, inline=False)
        if parsed_at:
            parsed_at = parsed_at[:parsed_at.index('.')]
            since = cf_common.pretty_time_format(now - int(clist.time_in_seconds(parsed_at)), only_most_significant=True)
            embed.add_field(name='Updated', value=f'{since} ago')
        
        return embed

    @staticmethod
    def _make_contest_embed_for_vc_ranklist(ranklist, vc_start_time=None, vc_end_time=None):
        contest = ranklist.contest
        embed = discord_common.cf_color_embed(title=contest.name, url=contest.url)
        embed.set_author(name='VC Standings')
        now = time.time()
        if vc_start_time and vc_end_time:
            en = '\N{EN SPACE}'
            elapsed = cf_common.pretty_time_format(now - vc_start_time, shorten=True)
            remaining = cf_common.pretty_time_format(max(0,vc_end_time - now), shorten=True)
            msg = f'{elapsed} elapsed{en}|{en}{remaining} remaining'
            embed.add_field(name='Tick tock', value=msg, inline=False)
        return embed

    async def resolve_contest(self, contest_id, resource):
        contest = None
        if resource=='clist.by':
            contest = await clist.contest(contest_id, with_problems=True)
        elif resource=='atcoder.jp':
            prefix = contest_id[:3]
            if prefix=='abc':
                prefix = 'AtCoder Beginner Contest '
            if prefix=='arc':
                prefix = 'AtCoder Regular Contest '
            if prefix=='agc':
                prefix = 'AtCoder Grand Contest '
            suffix = contest_id[3:]
            try:
                suffix = int(suffix)
            except:
                raise ContestCogError('Invalid contest_id provided.') 
            contest_name = prefix+str(suffix)
            contests = await clist.search_contest(regex=contest_name, resource=resource, with_problems=True)
            if contests==None or len(contests)==0:
                raise ContestCogError('Contest not found.')
            contest = contests[0] 
        elif resource=='codechef.com':
            contest_name = None
            if 'lunchtime' in contest_id:
                date = parse_date(contest_id[9:])
                contest_name = str(date.strftime('%B'))+' Lunchtime '+str(date.strftime('%Y'))
            elif 'cookoff' in contest_id:
                date = parse_date(contest_id[7:])
                contest_name = str(date.strftime('%B'))+' Cook-Off '+str(date.strftime('%Y'))
            elif 'long' in contest_id:
                date = parse_date(contest_id[4:])
                contest_name = str(date.strftime('%B'))+' Challenge '+str(date.strftime('%Y'))
            elif 'starters' in contest_id:
                date = parse_date(contest_id[8:])
                contest_name = str(date.strftime('%B'))+' CodeChef Starters '+str(date.strftime('%Y'))
            contests = await clist.search_contest(regex=contest_name, resource=resource, with_problems=True)
            if contests==None or len(contests)==0:
                raise ContestCogError('Contest not found.')
            contest = contests[0] 
        elif resource=='codingcompetitions.withgoogle.com' or resource=='facebook.com/hackercup':
            year,round = None,None
            contest_name = None
            if 'kickstart' in contest_id:
                year = contest_id[9:11]
                round = contest_id[11:]
                contest_name = 'Kick Start.*Round '+round
            elif 'codejam' in contest_id:
                year = contest_id[7:9]
                round = contest_id[9:]
                if round=='WF':
                    round = 'Finals'
                    contest_name = 'Code Jam.*Finals'
                elif round=='QR':
                    round = 'Qualification Round'
                    contest_name = 'Code Jam.*Qualification Round'
                else:
                    contest_name = 'Code Jam.*Round '+round
            elif 'hackercup' in contest_id:
                year = contest_id[9:11]
                round = contest_id[11:]
                if round=='WF':
                    round = 'Finals'
                    contest_name = 'Final Round '
                elif round=='QR':
                    round = 'Qualification Round'
                    contest_name = 'Qualification Round '
                else:
                    contest_name = 'Round '+round

            if not round:
                    raise ContestCogError('Invalid contest_id provided.') 
            try:
                year = int(year)
            except:
                raise ContestCogError('Invalid contest_id provided.') 
            start = dt.datetime(int('20'+str(year)), 1, 1)
            end = dt.datetime(int('20'+str(year+1)), 1, 1)
            date_limit = (start.strftime('%Y-%m-%dT%H:%M:%S'), end.strftime('%Y-%m-%dT%H:%M:%S'))
            contests = await clist.search_contest(regex=contest_name, resource=resource, date_limits=date_limit, with_problems=True)
            if contests==None or len(contests)==0:
                raise ContestCogError('Contest not found.')
            contest = contests[0]
        else:
            contests = await clist.search_contest(regex=contest_id, with_problems=True, order_by='-start')
            if contests==None or len(contests)==0:
                raise ContestCogError('Contest not found.')
            contest = contests[0]
            pass
        return contest

    @commands.command(brief='Show ranklist for given handles and/or server members',
        usage='[contest_name_regex / contest_id / -clist_contest_id] [handles...] [+top] [+server] [+list_name]')
    async def ranklist(self, ctx, contest_id: str, *handles: str):
        """Shows ranklist for the contest with given contest id/name. If handles contains
        '+server', all server members are included. No handles defaults to '+server'.
        
        # For codeforces ranklist
        ;ranklist codeforces_contest_id

        # For codechef ranklist
        ;ranklist [long/lunchtime/cookoff][mm][yyyy]

        # For atcoder ranklist
        ;ranklist [abc/arc/agc][number]

        # For google and facebook ranklist
        ;ranklist [kickstart/codejam/hackercup][yy][round]
        Use QR for Qualification Round and WF for World Finals.
        """
        msg = "Generating ranklist, please wait..."
        wait_msg = await ctx.channel.send(msg)
        resource = 'codeforces.com'
        timezone = cf_common.get_guild_timezone(ctx.guild.id)
        for pattern in _PATTERNS:
            if pattern in contest_id:
                resource = _PATTERNS[pattern]
                break
        if resource=='codeforces.com':
            try:
                contest_id = int(contest_id)
                if contest_id<0:
                    contest_id = -1*contest_id
                    resource = 'clist.by'
            except:
                resource = None
        if resource!='codeforces.com':
            contest = await self.resolve_contest(contest_id=contest_id, resource=resource)
            if contest is None:
                raise ContestCogError('Contest not found.') 
            contest_id = contest['id']
            resource = contest['resource']
            parsed_at = contest.get('parsed_at', None);
            selected_divs = []
            handles = list(handles)
            if resource=='codechef.com':
                divs = {'+div1': 'div_1', '+div2': 'div_2', '+div3': 'div_3'}
                for div in divs.keys():
                    if div in handles:
                        handles.remove(div)
                        selected_divs.append(divs[div])
            show_top_50 = False
            if "+top" in handles:
                show_top_50 = True
                handles.remove("+top")
                account_ids = None
            else:
                account_ids= await cf_common.resolve_handles(ctx, self.member_converter, handles, maxcnt=None, default_to_all_server=True, resource=contest['resource'])
            users = {}
            if resource=='codedrills.io':
                clist_users = await clist.fetch_user_info(resource, account_ids)
                for clist_user in clist_users:
                    users[clist_user['id']] = clist_user['name']
            standings_to_show = []
            standings = await clist.statistics(contest_id=contest_id, account_ids=account_ids, with_extra_fields=True, with_problems=True, order_by='place', limit=50 if show_top_50 else 1000)
            for standing in standings:
                if not standing['place'] or not standing['handle']:
                    continue
                if resource=='codedrills.io':
                    standing['handle'] = users[standing['account_id']] or ''
                elif resource=='facebook.com/hackercup':
                    more_fields = standing.get('more_fields')
                    if more_fields:
                        name = more_fields['name']
                        if '(' in name and ')' in name:
                            name = name[:name.index('(')]
                        standing['handle'] = name;
                elif resource=='codechef.com':
                    if 'more_fields' in standing and 'division' in standing['more_fields']:
                        if len(selected_divs)!=0 and standing['more_fields']['division'] not in selected_divs:
                            continue
                standings_to_show.append(standing)
            standings_to_show.sort(key=lambda standing: int(standing['place']))
            if len(standings_to_show)==0:
                if parsed_at:
                    name = contest['event']
                    raise ContestCogError(f'None of the handles are present in the ranklist of `{name}`') 
                else:
                    raise ContestCogError('Ranklist for this contest is being parsed, please come back later.') 
            division = selected_divs[0] if len(selected_divs)==1 else None
            problemset = contest.get('problems', None);
            pages = self._make_clist_standings_pages(standings_to_show, problemset=problemset, division=division)
            await wait_msg.delete()
            await ctx.channel.send(embed=self._make_contest_embed_for_ranklist(contest=clist.format_contest(contest), timezone=timezone, parsed_at=parsed_at))
            paginator.paginate(self.bot, ctx.channel, pages, wait_time=_STANDINGS_PAGINATE_WAIT_TIME)
        else:
            if (int(contest_id) == 4):
                await ctx.channel.send("```I\'m not doing that! (╯°□°）╯︵ ┻━┻ ```""")
            else:
                handles = await cf_common.resolve_handles(ctx, self.member_converter, handles, maxcnt=None, default_to_all_server=True)
                contest = cf_common.cache2.contest_cache.get_contest(contest_id)
                ranklist = None
                try:
                    ranklist = cf_common.cache2.ranklist_cache.get_ranklist(contest)
                except cache_system2.RanklistNotMonitored:
                    if contest.phase == 'BEFORE':
                        raise ContestCogError(f'Contest `{contest.id} | {contest.name}` has not started')
                    ranklist = await cf_common.cache2.ranklist_cache.generate_ranklist(contest.id,
                                                                                    fetch_changes=True)
                await wait_msg.delete()
                await ctx.channel.send(embed=self._make_contest_embed_for_ranklist(ranklist, timezone=timezone))
                await self._show_ranklist(channel=ctx.channel, contest_id=contest_id, handles=handles, ranklist=ranklist)

    async def _show_ranklist(self, channel, contest_id: int, handles: List[str], ranklist, vc: bool = False, delete_after: float = None):
        contest = cf_common.cache2.contest_cache.get_contest(contest_id)
        if ranklist is None:
            raise ContestCogError('No ranklist to show')

        handle_standings = []
        for handle in handles:
            try:
                standing = ranklist.get_standing_row(handle)
            except rl.HandleNotPresentError:
                continue

            # Database has correct handle ignoring case, update to it
            # TODO: It will throw an exception if this row corresponds to a team. At present ranklist doesnt show teams.
            # It should be fixed in https://github.com/cheran-senthil/TLE/issues/72
            handle = standing.party.members[0].handle
            if vc and standing.party.participantType != 'VIRTUAL':
                continue
            handle_standings.append((handle, standing))

        if not handle_standings:
            error = f'None of the handles are present in the ranklist of `{contest.name}`'
            if vc:
                await channel.send(embed=discord_common.embed_alert(error), delete_after=delete_after)
                return
            raise ContestCogError(error)

        handle_standings.sort(key=lambda data: data[1].rank)
        deltas = None
        if ranklist.is_rated:
            deltas = [ranklist.get_delta(handle) for handle, standing in handle_standings]

        problem_indices = [problem.index for problem in ranklist.problems]
        pages = self._make_standings_pages(contest, problem_indices, handle_standings, deltas)
        paginator.paginate(self.bot, channel, pages, wait_time=_STANDINGS_PAGINATE_WAIT_TIME, delete_after=delete_after)

    @commands.command(brief='Start a rated vc for people who have reacted to a message.', usage='<contest_id> <message url>')
    async def ratedvcfor(self, ctx, contest_id: int, message_url:str):
        message_converter = commands.MessageConverter()
        try:
            message = await message_converter.convert(ctx, message_url)
        except commands.errors.CommandError:
            raise ContestCogError('Failed to resolve message_url')
        members = []
        for reaction in message.reactions:
            users = await reaction.users().flatten()
            members+=users
        await self.ratedvc(ctx, contest_id, *members)

    @commands.command(brief='Start a rated vc.', usage='<contest_id> <@user1 @user2 ...>')
    async def ratedvc(self, ctx, contest_id: int, *members: discord.Member):
        ratedvc_channel_id = cf_common.user_db.get_rated_vc_channel(ctx.guild.id)
        if not ratedvc_channel_id or ctx.channel.id != ratedvc_channel_id:
            raise ContestCogError('You must use this command in ratedvc channel.')
        if not members:
            raise ContestCogError('Missing members')
        contest = cf_common.cache2.contest_cache.get_contest(contest_id)
        try:
            (await cf.contest.ratingChanges(contest_id=contest_id))[_MIN_RATED_CONTESTANTS_FOR_RATED_VC - 1]
        except (cf.RatingChangesUnavailableError, IndexError):
            error = (f'`{contest.name}` was not rated for at least {_MIN_RATED_CONTESTANTS_FOR_RATED_VC} contestants'
                    ' or the ratings changes are not published yet.')
            raise ContestCogError(error)

        ongoing_vc_member_ids = _get_ongoing_vc_participants()
        this_vc_member_ids = {str(member.id) for member in members}
        intersection = this_vc_member_ids & ongoing_vc_member_ids
        if intersection:
            busy_members = ", ".join([ctx.guild.get_member(int(member_id)).mention for member_id in intersection])
            error = f'{busy_members} are registered in ongoing ratedvcs.'
            raise ContestCogError(error)

        handles = cf_common.members_to_handles(members, ctx.guild.id)
        visited_contests = await cf_common.get_visited_contests(handles)
        if contest_id in visited_contests:
            raise ContestCogError(f'Some of the handles: {", ".join(handles)} have submissions in the contest')
        start_time = time.time()
        finish_time = start_time + contest.durationSeconds + _RATED_VC_EXTRA_TIME
        cf_common.user_db.create_rated_vc(contest_id, start_time, finish_time, ctx.guild.id, [member.id for member in members])
        title = f'Starting {contest.name} for:'
        msg = "\n".join(f'[{discord.utils.escape_markdown(handle)}]({cf.PROFILE_BASE_URL}{handle})' for handle in handles)
        embed = discord_common.cf_color_embed(title=title, description=msg, url=contest.url)
        await ctx.send(embed=embed)
        embed = discord_common.embed_alert(f'You have {int(finish_time - start_time) // 60} minutes to complete the vc!')
        embed.set_footer(text='GL & HF')
        await ctx.send(embed=embed)

    @staticmethod
    def _make_vc_rating_changes_embed(guild, contest_id, change_by_handle):
        """Make an embed containing a list of rank changes and rating changes for ratedvc participants.
        """
        contest = cf_common.cache2.contest_cache.get_contest(contest_id)
        user_id_handle_pairs = cf_common.user_db.get_handles_for_guild(guild.id)
        member_handle_pairs = [(guild.get_member(int(user_id)), handle)
                               for user_id, handle in user_id_handle_pairs]
        member_change_pairs = [(member, change_by_handle[handle])
                               for member, handle in member_handle_pairs
                               if member is not None and handle in change_by_handle]

        member_change_pairs.sort(key=lambda pair: pair[1].newRating, reverse=True)
        rank_to_role = {role.name: role for role in guild.roles}

        def rating_to_displayable_rank(rating):
            rank = cf.rating2rank(rating).title
            role = rank_to_role.get(rank)
            return role.mention if role else rank

        rank_changes_str = []
        for member, change in member_change_pairs:
            if len(cf_common.user_db.get_vc_rating_history(member.id)) == 1:
                # If this is the user's first rated contest.
                old_role = 'Unrated'
            else:
                old_role = rating_to_displayable_rank(change.oldRating)
            new_role = rating_to_displayable_rank(change.newRating)
            if new_role != old_role:
                rank_change_str = (f'{member.mention} [{discord.utils.escape_markdown(change.handle)}]({cf.PROFILE_BASE_URL}{change.handle}): {old_role} '
                                   f'\N{LONG RIGHTWARDS ARROW} {new_role}')
                rank_changes_str.append(rank_change_str)

        member_change_pairs.sort(key=lambda pair: pair[1].newRating - pair[1].oldRating,
                                 reverse=True)
        rating_changes_str = []
        for member, change in member_change_pairs:
            delta = change.newRating - change.oldRating
            rating_change_str = (f'{member.mention} [{discord.utils.escape_markdown(change.handle)}]({cf.PROFILE_BASE_URL}{change.handle}): {change.oldRating} '
                            f'\N{HORIZONTAL BAR} **{delta:+}** \N{LONG RIGHTWARDS ARROW} '
                            f'{change.newRating}')
            rating_changes_str.append(rating_change_str)

        desc = '\n'.join(rank_changes_str) or 'No rank changes'
        embed = discord_common.cf_color_embed(title=contest.name, url=contest.url, description=desc)
        embed.set_author(name='VC Results')
        embed.add_field(name='Rating Changes',
                        value='\n'.join(rating_changes_str) or 'No rating changes',
                        inline=False)
        return embed

    async def _watch_rated_vc(self, vc_id: int):
        vc = cf_common.user_db.get_rated_vc(vc_id)
        channel_id = cf_common.user_db.get_rated_vc_channel(vc.guild_id)
        if channel_id is None:
            raise ContestCogError('No Rated VC channel')
        channel = self.bot.get_channel(int(channel_id))
        member_ids = cf_common.user_db.get_rated_vc_user_ids(vc_id)
        handles = [cf_common.user_db.get_handle(member_id, channel.guild.id) for member_id in member_ids]
        handle_to_member_id = {handle : member_id for handle, member_id in zip(handles, member_ids)}
        now = time.time()
        ranklist = await cf_common.cache2.ranklist_cache.generate_vc_ranklist(vc.contest_id, handle_to_member_id)

        async def has_running_subs(handle):
            return [sub for sub in await cf.user.status(handle=handle)
                    if sub.verdict == 'TESTING' and
                       sub.problem.contestId == vc.contest_id and
                       sub.relativeTimeSeconds <= vc.finish_time - vc.start_time]

        running_subs_flag = any([await has_running_subs(handle) for handle in handles])
        if running_subs_flag:
            msg = 'Some submissions are still being judged'
            await channel.send(embed=discord_common.embed_alert(msg), delete_after=_WATCHING_RATED_VC_WAIT_TIME)
        if now < vc.finish_time or running_subs_flag:
            # Display current standings
            await channel.send(embed=self._make_contest_embed_for_vc_ranklist(ranklist, vc.start_time, vc.finish_time), delete_after=_WATCHING_RATED_VC_WAIT_TIME)
            await self._show_ranklist(channel, vc.contest_id, handles, ranklist=ranklist, vc=True, delete_after=_WATCHING_RATED_VC_WAIT_TIME)
            return
        rating_change_by_handle = {}
        RatingChange = namedtuple('RatingChange', 'handle oldRating newRating')
        for handle, member_id in zip(handles, member_ids):
            delta = ranklist.delta_by_handle.get(handle)
            if delta is None:  # The user did not participate.
                cf_common.user_db.remove_last_ratedvc_participation(member_id)
                continue
            old_rating = cf_common.user_db.get_vc_rating(member_id)
            new_rating = old_rating + delta
            rating_change_by_handle[handle] = RatingChange(handle=handle, oldRating=old_rating, newRating=new_rating)
            cf_common.user_db.update_vc_rating(vc_id, member_id, new_rating)
        cf_common.user_db.finish_rated_vc(vc_id)
        await channel.send(embed=self._make_vc_rating_changes_embed(channel.guild, vc.contest_id, rating_change_by_handle))
        await self._show_ranklist(channel, vc.contest_id, handles, ranklist=ranklist, vc=True)

    @tasks.task_spec(name='WatchRatedVCs',
                     waiter=tasks.Waiter.fixed_delay(_WATCHING_RATED_VC_WAIT_TIME))
    async def _watch_rated_vcs_task(self, _):
        ongoing_rated_vcs = cf_common.user_db.get_ongoing_rated_vc_ids()
        if ongoing_rated_vcs is None:
            return
        for rated_vc_id in ongoing_rated_vcs:
            await self._watch_rated_vc(rated_vc_id)

    @commands.command(brief='Unregister this user from an ongoing ratedvc', usage='@user')
    @commands.check_any(commands.has_any_role('Admin', constants.TLE_MODERATOR), commands.is_owner())
    async def _unregistervc(self, ctx, user: discord.Member):
        """ Unregister this user from an ongoing ratedvc.
        """
        ongoing_vc_member_ids = _get_ongoing_vc_participants()
        if str(user.id) not in ongoing_vc_member_ids:
            raise ContestCogError(f'{user.mention} has no ongoing ratedvc!')
        cf_common.user_db.remove_last_ratedvc_participation(user.id)
        await ctx.send(embed=discord_common.embed_success(f'Successfully unregistered {user.mention} from the ongoing vc.'))

    @commands.command(brief='Set the rated vc channel to the current channel')
    @commands.check_any(commands.has_role('Admin'), commands.is_owner())
    async def set_ratedvc_channel(self, ctx):
        """ Sets the rated vc channel to the current channel.
        """
        cf_common.user_db.set_rated_vc_channel(ctx.guild.id, ctx.channel.id)
        await ctx.send(embed=discord_common.embed_success('Rated VC channel saved successfully'))

    @commands.command(brief='Get the rated vc channel')
    async def get_ratedvc_channel(self, ctx):
        """ Gets the rated vc channel.
        """
        channel_id = cf_common.user_db.get_rated_vc_channel(ctx.guild.id)
        channel = ctx.guild.get_channel(channel_id)
        if channel is None:
            raise ContestCogError('There is no rated vc channel')
        embed = discord_common.embed_success('Current rated vc channel')
        embed.add_field(name='Channel', value=channel.mention)
        await ctx.send(embed=embed)

    @commands.command(brief='Show vc ratings')
    async def vcratings(self, ctx):
        users = [(await self.member_converter.convert(ctx, str(member_id)), handle, cf_common.user_db.get_vc_rating(member_id, default_if_not_exist=False))
                 for member_id, handle in cf_common.user_db.get_handles_for_guild(ctx.guild.id)]
        # Filter only rated users. (Those who entered at least one rated vc.)
        users = [(member, handle, rating)
                 for member, handle, rating in users
                 if rating is not None]
        users.sort(key=lambda user: -user[2])

        _PER_PAGE = 10

        def make_page(chunk, page_num):
            style = table.Style('{:>}  {:<}  {:<}  {:<}')
            t = table.Table(style)
            t += table.Header('#', 'Name', 'Handle', 'Rating')
            t += table.Line()
            for index, (member, handle, rating) in enumerate(chunk):
                rating_str = f'{rating} ({cf.rating2rank(rating).title_abbr})'
                t += table.Data(_PER_PAGE * page_num + index, f'{member.display_name}', handle, rating_str)

            table_str = f'```\n{t}\n```'
            embed = discord_common.cf_color_embed(description=table_str)
            return 'VC Ratings', embed

        if not users:
            raise ContestCogError('There are no active VCers.')

        pages = [make_page(chunk, k) for k, chunk in enumerate(paginator.chunkify(users, _PER_PAGE))]
        paginator.paginate(self.bot, ctx.channel, pages, wait_time=5 * 60, set_pagenum_footers=True)

    @commands.command(brief='Plot vc rating for a list of at most 5 users', usage='@user1 @user2 ..')
    async def vcrating(self, ctx, *members: discord.Member):
        """Plots VC rating for at most 5 users."""
        members = members or (ctx.author, )
        if len(members) > 5:
            raise ContestCogError('Cannot plot more than 5 VCers at once.')
        plot_data = defaultdict(list)

        min_rating = 1100
        max_rating = 1800

        for member in members:
            rating_history = cf_common.user_db.get_vc_rating_history(member.id)
            if not rating_history:
                raise ContestCogError(f'{member.mention} has no vc history.')
            for vc_id, rating in rating_history:
                vc = cf_common.user_db.get_rated_vc(vc_id)
                date = dt.datetime.fromtimestamp(vc.finish_time)
                plot_data[member.display_name].append((date, rating))
                min_rating = min(min_rating, rating)
                max_rating = max(max_rating, rating)

        plt.clf()
        # plot at least from mid gray to mid purple
        for rating_data in plot_data.values():
            x, y = zip(*rating_data)
            plt.plot(x, y,
                     linestyle='-',
                     marker='o',
                     markersize=4,
                     markerfacecolor='white',
                     markeredgewidth=0.5)

        gc.plot_rating_bg(cf.RATED_RANKS)
        plt.gcf().autofmt_xdate()

        plt.ylim(min_rating - 100, max_rating + 200)
        labels = [
            gc.StrWrap('{} ({})'.format(
                member_display_name,
                rating_data[-1][1]))
            for member_display_name, rating_data in plot_data.items()
        ]
        plt.legend(labels, loc='upper left', prop=gc.fontprop)

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='VC rating graph')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, ctx.author)
        await ctx.send(embed=embed, file=discord_file)

    @discord_common.send_error_if(ContestCogError, rl.RanklistError,
                                  cache_system2.CacheError, cf_common.ResolveHandleError)
    async def cog_command_error(self, ctx, error):
        pass

    @commands.command(brief='Plot vc performance for a list of at most 5 users', usage='@user1 @user2 ..')
    async def vcperformance(self, ctx, *members: discord.Member):
        """Plots VC performance for at most 5 users."""
        members = members or (ctx.author, )
        if len(members) > 5:
            raise ContestCogError('Cannot plot more than 5 VCers at once.')
        plot_data = defaultdict(list)

        min_rating = 1100
        max_rating = 1800

        for member in members:
            rating_history = cf_common.user_db.get_vc_rating_history(member.id)
            if not rating_history:
                raise ContestCogError(f'{member.mention} has no vc history.')
            ratingbefore = 100
            for vc_id, rating in rating_history:
                vc = cf_common.user_db.get_rated_vc(vc_id)
                perf = ratingbefore + (rating - ratingbefore)*4
                date = dt.datetime.fromtimestamp(vc.finish_time)
                plot_data[member.display_name].append((date, perf))
                min_rating = min(min_rating, perf)
                max_rating = max(max_rating, perf)
                ratingbefore = rating

        plt.clf()
        # plot at least from mid gray to mid purple
        for rating_data in plot_data.values():
            x, y = zip(*rating_data)
            plt.plot(x, y,
                     linestyle='-',
                     marker='o',
                     markersize=4,
                     markerfacecolor='white',
                     markeredgewidth=0.5)

        gc.plot_rating_bg(cf.RATED_RANKS)
        plt.gcf().autofmt_xdate()

        plt.ylim(min_rating - 100, max_rating + 200)
        labels = [
            gc.StrWrap('{} ({})'.format(
                member_display_name,
                ratingbefore))
            for member_display_name, rating_data in plot_data.items()
        ]
        plt.legend(labels, loc='upper left', prop=gc.fontprop)

        discord_file = gc.get_current_figure_as_file()
        embed = discord_common.cf_color_embed(title='VC performance graph')
        discord_common.attach_image(embed, discord_file)
        discord_common.set_author_footer(embed, ctx.author)
        await ctx.send(embed=embed, file=discord_file)

    @discord_common.send_error_if(ContestCogError, rl.RanklistError,
                                  cache_system2.CacheError, cf_common.ResolveHandleError)
    async def cog_command_error(self, ctx, error):
        pass



def setup(bot):
    bot.add_cog(Contests(bot))
