import logging
import json
import discord
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

    @commands.command(brief='Command to ban users from accessing the bot', hidden=True)
    @commands.is_owner()
    async def ban(self, ctx, member: discord.Member):
        cf_common.user_db.ban_user(member.id)
        return await ctx.send("```"+str(member.display_name)+" banned from TLE!!!```")
    
    @commands.command(brief='Command to unban users', hidden=True)
    @commands.is_owner()
    async def unban(self, ctx, member: discord.Member):
        cf_common.user_db.unban_user(member.id)
        return await ctx.send("```"+str(member.display_name)+" unbanned!!! ```")
   
    @discord_common.send_error_if(HandleCogError, cf_common.HandleIsVjudgeError)
    async def cog_command_error(self, ctx, error):
        pass


def setup(bot):
    bot.add_cog(HandleLists(bot))
