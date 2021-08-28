import logging
import json
from discord.ext import commands

from tle.util import codeforces_common as cf_common
from tle.util import paginator
from tle.util import table
from tle.util import clist_api as clist
from tle import constants
from tle.util import discord_common
from tle.cogs.handles import HandleCogError,_CLIST_RESOURCE_SHORT_FORMS, _HANDLES_PER_PAGE, _PAGINATE_WAIT_TIME,_SUPPORTED_CLIST_RESOURCES, resource_name
from discord.ext import commands

def _make_pages(users, title, resource='codeforces.com'):
    chunks = paginator.chunkify(users, _HANDLES_PER_PAGE)
    pages = []
    done = 1
    no_rating = resource in ['codingcompetitions.withgoogle.com', 'facebook.com/hackercup']
    style = table.Style('{:>}  {:<}  {:<}')
    for chunk in chunks:
        t = table.Table(style)
        t += table.Header('#', 'Handle', 'Contests' if no_rating else 'Rating')
        t += table.Line()
        for i, (handle, rating, n_contests) in enumerate(chunk):
            rating_str = 'N/A' if rating is None else str(rating)
            third = n_contests if no_rating else (f'{rating_str}')
            t += table.Data(i + done, handle, third)
        table_str = '```\n'+str(t)+'\n```'
        embed = discord_common.cf_color_embed(description=table_str)
        pages.append((title, embed))
        done += len(chunk)
    return pages

class HandleLists(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(self.__class__.__name__)
        self.converter = commands.MemberConverter()

    @commands.Cog.listener()
    @discord_common.once
    async def on_ready(self):
        pass

    @commands.group(brief='Commands that have to do with lists', invoke_without_command=True, hidden=True)
    async def list(self, ctx):
        """Change or create list of handles"""
        await ctx.send_help(ctx.command)


    @commands.check_any(commands.has_any_role('Admin', constants.TLE_MODERATOR), commands.is_owner())
    @list.command(brief='To create a handle list')    
    async def create(self, ctx, list_name):
        cf_common.user_db.create_list(ctx.guild.id, list_name)
        await ctx.send("```List created!!!```")
    

    @commands.check_any(commands.has_any_role('Admin', constants.TLE_MODERATOR), commands.is_owner())
    @list.command(brief='To delete a handle list')    
    async def delete(self, ctx, list_name):
        cf_common.user_db.delete_list(ctx.guild.id, list_name)
        await ctx.send("```List deleted!!!```")
    
    
    @list.command(brief='To add a handle to a list')    
    async def add(self, ctx, list_name:str, resource:str, *handles:str):
        if resource in _CLIST_RESOURCE_SHORT_FORMS:
            resource = _CLIST_RESOURCE_SHORT_FORMS[resource]
        if resource!='codeforces.com' and resource not in _SUPPORTED_CLIST_RESOURCES:
            raise HandleCogError(f'The resource `{resource}` is not supported.')
        lists = cf_common.user_db.get_lists(ctx.guild.id)
        if list_name not in lists:
            raise HandleCogError(f'List not found!!!')
        clist_users = await clist.fetch_user_info(resource=resource, handles=handles)
        message = 'Added '
        for user in clist_users:
            message += str(user['handle'])+", "
            cf_common.user_db.add_to_list(list_name=list_name, resource=resource, account_id=user['id'], handle=user['handle'])
        await ctx.send("```"+message+" to "+list_name+"```")
        
    @list.command(brief='To remove a handle from a list')    
    async def remove(self, ctx, list_name:str, resource:str, *handles:str):
        if resource in _CLIST_RESOURCE_SHORT_FORMS:
            resource = _CLIST_RESOURCE_SHORT_FORMS[resource]
        if resource!='codeforces.com' and resource not in _SUPPORTED_CLIST_RESOURCES:
            raise HandleCogError(f'The resource `{resource}` is not supported.')
        lists = cf_common.user_db.get_lists(ctx.guild.id)
        if list_name not in lists:
            raise HandleCogError(f'List not found!!!')
        message = 'Removed '
        for handle in handles:
            res = cf_common.user_db.remove_from_list(list_name=list_name, handle=handle, resource=resource)
            if res:
                message += str(handle)+", "
        await ctx.send("```"+message+" from "+list_name+"```")

    @list.command(brief='To view a handle list')    
    async def view(self, ctx, list_name, resource):
        if resource in _CLIST_RESOURCE_SHORT_FORMS:
            resource = _CLIST_RESOURCE_SHORT_FORMS[resource]
        if resource!='codeforces.com' and resource not in _SUPPORTED_CLIST_RESOURCES:
            raise HandleCogError(f'The resource `{resource}` is not supported.')
        lists = cf_common.user_db.get_lists(ctx.guild.id)
        if list_name not in lists:
            raise HandleCogError(f'List not found!!!')
        wait_msg = await ctx.channel.send('Fetching handles, please wait...')
        ids = cf_common.user_db.get_list_account_ids(list_name=list_name ,resource=resource)
        
        users = []
        if ids!=None and len(ids)>0:
            clist_users = await clist.fetch_user_info(resource, ids)
            for clist_user in clist_users:
                handle = clist_user['handle']
                if resource in ['codedrills.io', 'facebook.com/hackercup']:
                    name = clist_user['name']
                    if '(' in name and ')' in name:
                        name = name[:name.index('(')]
                    handle = name or ' '
                rating = int(clist_user['rating']) if clist_user['rating']!=None else None
                n_contests = clist_user['n_contests']
                users.append((handle, rating, n_contests))
        if not users:
            raise HandleCogError('No handles present in list.')

        users.sort(key=lambda x: (1 if x[1] is None else -x[1], -x[2],x[0]))  # Sorting by (-rating,-contests, handle)
        title = f'Handles of list {list_name} ({resource_name(resource)})'
        pages = _make_pages(users, title, resource)
        await wait_msg.delete()
        paginator.paginate(self.bot, ctx.channel, pages, wait_time=_PAGINATE_WAIT_TIME,
                           set_pagenum_footers=True)
    
    @list.command(brief='To view all lists')
    async def all(self, ctx):
        lists = cf_common.user_db.get_lists(ctx.guild.id)
        if lists is None:
            raise HandleCogError(f'No lists created yet.')
        print(lists)
        message = ','.join(lists)
        await ctx.send("```The following lists are present in "+ctx.guild.name+"\n"+message+"```")
    @discord_common.send_error_if(HandleCogError, cf_common.HandleIsVjudgeError)
    async def cog_command_error(self, ctx, error):
        pass


def setup(bot):
    bot.add_cog(HandleLists(bot))
