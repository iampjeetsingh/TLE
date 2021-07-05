import logging
import json
from discord.ext import commands

from tle.util import codeforces_common as cf_common
from tle import constants
from tle.util import discord_common
from tle.cogs.handles import HandleCogError,_CLIST_RESOURCE_SHORT_FORMS,_SUPPORTED_CLIST_RESOURCES
from discord.ext import commands


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
    @commands.check_any(commands.has_any_role('Admin', constants.TLE_MODERATOR), commands.is_owner())
    async def list(self, ctx):
        """Change or create list of handles"""
        await ctx.send_help(ctx.command)

    @list.command(brief='To create a handle list')    
    async def create(self, ctx, list_name):
        handles = []
        cf_common.user_db.create_list(ctx.guild.id, list_name, json.dumps(handles))
        await ctx.send("list created!!!")
    
    @list.command(brief='To delete a handle list')    
    async def delete(self, ctx, list_name):
        cf_common.user_db.delete_list(ctx.guild.id, list_name)
        await ctx.send("list deleted!!!")
    
    
    @list.command(brief='To add a handle to a list')    
    async def add(self, ctx, list_name, *handles):
        guild_id = ctx.guild.id
        handle_list = cf_common.user_db.get_list(guild_id, list_name)
        if handle_list is None:
            return await ctx.send("list not found!!!")
        handle_list = json.loads(handle_list)
        handle_list += handles
        cf_common.user_db.create_list(guild_id, list_name, json.dumps(handle_list))
        await ctx.send("list updated!!!")
        
    @list.command(brief='To remove a handle from a list')    
    async def remove(self, ctx, list_name, *handles):
        guild_id = ctx.guild.id
        handle_list = cf_common.user_db.get_list(guild_id, list_name)
        if handle_list is None:
            return await ctx.send("list not found!!!")
        handle_list = json.loads(handle_list)
        for handle in handles:
            handle_list.remove(handle)
        cf_common.user_db.create_list(guild_id, list_name, json.dumps(handle_list))
        await ctx.send("list updated!!!")

    @list.command(brief='To view a handle list')    
    async def view(self, ctx, list_name):
        guild_id = ctx.guild.id
        handle_list = cf_common.user_db.get_list(guild_id, list_name)
        if handle_list is None:
            return await ctx.send("list not found!!!")
        handle_list = json.loads(handle_list)
        message = "list "+str(list_name)
        for handle in handle_list:
            message += "\n"+str(handle)
        await ctx.send(message)
    
   
    @discord_common.send_error_if(HandleCogError, cf_common.HandleIsVjudgeError)
    async def cog_command_error(self, ctx, error):
        pass


def setup(bot):
    bot.add_cog(HandleLists(bot))
