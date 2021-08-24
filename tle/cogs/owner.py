import logging
import json
import discord
from discord.ext import commands

from tle.util import codeforces_common as cf_common
from tle.util import codeforces_api as cf
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

    @commands.group(brief='Commands related to daily practice problems', hidden=True, invoke_without_command=True)
    @commands.check_any(commands.has_any_role('Admin', constants.TLE_MODERATOR), commands.is_owner())
    async def dpp(self, ctx):
        await ctx.send_help(ctx.command)

    @dpp.command(brief='For uploading new practice problems', usage='<role> <date/description> [links...] [+rating]')
    async def upload(self, ctx, role:discord.Role, day,*links):
        links = list(links)
        show_rating = '+rating' in links
        if show_rating:
            links.remove('+rating')
        levels = []
        level = []
        for i,link in enumerate(links):
            if i==len(links)-1:
                level.append(link)
                levels.append(level)
            elif link=='|':
                levels.append(level)
                level = []
            else:
                level.append(link)
        embed = discord_common.cf_color_embed(title='Daily Practice Problems', description=day)
        for i, level in enumerate(levels):
            text = ''
            for j, link in enumerate(level):
                skip = False
                if link[0]=='?':
                    skip = True
                    link = link[1:]
                parts = link.split('/')
                if 'codeforces.com' in parts and not skip:
                    problem_index = parts[-1]
                    contest_id = parts[-3] if 'contest' in parts else parts[-2]
                    _, problems, _ = await cf.contest.standings(contest_id=contest_id,
                                                                            show_unofficial=False)
                    problem = None
                    for prob in problems:
                        if prob.index==problem_index:
                            problem = prob
                            break
                    if problem:
                        rating = f' [{problem.rating}]' if show_rating else ''
                        text += f'[{problem.name}]({problem.url}){rating}\n'
                else:
                    text += f'[Problem {j+1}]({link})\n'
            if len(levels)>1:
                embed.add_field(name=f'Level {i+1}', value=text, inline=False)
            else:
                text = f'{day}\n\n{text}'
                embed = discord_common.cf_color_embed(title='Daily Practice Problems', description=text)
        embed.set_footer(text='All the best!!!')
        message = f'{role.mention}'
        await ctx.message.delete()
        await ctx.send(message, embed=embed)
   
    @discord_common.send_error_if(HandleCogError, cf_common.HandleIsVjudgeError)
    async def cog_command_error(self, ctx, error):
        pass


def setup(bot):
    bot.add_cog(HandleLists(bot))
