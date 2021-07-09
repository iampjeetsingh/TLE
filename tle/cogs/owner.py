import logging
import json
import discord
from discord.ext import commands

from tle.util import codeforces_common as cf_common
from tle import constants
from tle.util import discord_common
from tle.cogs.handles import HandleCogError,_CLIST_RESOURCE_SHORT_FORMS,_SUPPORTED_CLIST_RESOURCES
from tle.cogs.handles import CODECHEF_RATED_RANKS
from tle.util.codeforces_api import RATED_RANKS as CODEFORCES_RATED_RANKS
from discord.ext import commands

async def _create_roles(ctx, ranks):
    for rank in ranks[::-1]:
        guild = ctx.guild
        await guild.create_role(name=rank.title, colour=discord.Colour(rank.color_embed))

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
    
    @commands.group(brief='Command to create roles for codeforces/codechef', hidden=True, invoke_without_command=True)
    @commands.check_any(commands.has_any_role('Admin', constants.TLE_MODERATOR), commands.is_owner())
    async def createroles(self, ctx):
        await ctx.send_help(ctx.command)
    
    @createroles.command(brief='Create roles for codeforces ranks')
    async def codeforces(self, ctx):
        wait_msg = await ctx.channel.send("Creating Roles...")
        await _create_roles(ctx, CODEFORCES_RATED_RANKS)
        await wait_msg.delete()
        await ctx.send(embed=discord_common.embed_success('Roles created successfully.'))

    @createroles.command(brief='Create roles for codechef stars')
    async def codechef(self, ctx):
        wait_msg = await ctx.channel.send("Creating Roles...")
        await _create_roles(ctx, CODECHEF_RATED_RANKS)
        await wait_msg.delete()
        await ctx.send(embed=discord_common.embed_success('Roles created successfully.'))
   
    @discord_common.send_error_if(HandleCogError, cf_common.HandleIsVjudgeError)
    async def cog_command_error(self, ctx, error):
        pass


def setup(bot):
    bot.add_cog(HandleLists(bot))
